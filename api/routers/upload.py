"""
api/routers/upload.py — streamed upload + indexing progress
================================================================
Switched from a single blocking JSON response to SSE so the frontend can
show real stage transitions (extracting text -> generating embeddings ->
indexing) instead of a single indeterminate spinner for the whole
request/response cycle. The multipart file itself still arrives as a normal
request body — XHR on the frontend tracks byte-upload progress via
xhr.upload.onprogress the same way as before; this only changes what comes
back afterward.
"""

import json
import logging
import tempfile
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import db as authdb
from api.deps import require_uploader, get_current_user
from api.services import index_uploaded_file_streaming

router = APIRouter(prefix="/api/upload", tags=["upload"])
logger = logging.getLogger("hawkins.upload")


class DuplicateCheckRequest(BaseModel):
    filename: str
    doc_id: str  # SHA-1 of the file's bytes, computed client-side (Web Crypto),
                 # truncated to the same 16 hex chars as pipeline/doc_id.py —
                 # sending only the hash, not the file, keeps this check cheap
                 # enough to run before the real upload even starts.


@router.post("/check")
def check_duplicate(body: DuplicateCheckRequest, user: dict = Depends(get_current_user)):
    """
    Pre-upload duplicate check — filename + content-hash, the two fast/
    deterministic stages of exact-duplicate detection. Deliberately does
    NOT check against every file in the system: it's scoped to
    allowed_doc_ids(user), the same set the person could already find
    through search. Checking org-wide would let an upload confirm the
    existence (and filename) of a document the uploader has no access to —
    a real information leak for a filename check that's meant to save a
    little re-processing time, not act as a side-channel into other
    departments' files.
    """
    allowed = authdb.allowed_doc_ids(user)
    files = authdb.list_files()
    if allowed is not None:
        files = [f for f in files if f["doc_id"] in allowed]

    exact = next((f for f in files if f["doc_id"] == body.doc_id), None)
    if exact:
        return {"verdict": "exact_duplicate", "existing": _slim(exact)}

    name_conflict = next(
        (f for f in files if f["source"].lower() == body.filename.lower() and f["doc_id"] != body.doc_id),
        None,
    )
    if name_conflict:
        return {"verdict": "same_name_conflict", "existing": _slim(name_conflict)}

    return {"verdict": "ok", "existing": None}


def _slim(f: dict) -> dict:
    return {
        "doc_id": f["doc_id"],
        "source": f["source"],
        "uploaded_by": f.get("uploaded_by"),
        "created_at": f.get("created_at"),
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream_upload(tmp_path: str, filename: str, acl: dict):
    try:
        for event in index_uploaded_file_streaming(tmp_path, filename, acl):
            yield _sse(event)
    except Exception as e:
        logger.exception("Upload indexing failed for %s", filename)
        yield _sse({"stage": "error", "message": str(e)})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    dept_ids: str = Form("[]"),   # JSON-encoded list[int], e.g. "[1,3]"
    is_public: bool = Form(False),
    user: dict = Depends(require_uploader),
):
    try:
        parsed_dept_ids = json.loads(dept_ids)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "dept_ids must be a JSON array of ints")

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    acl = {
        "uploaded_by": user["username"],
        "dept_ids": parsed_dept_ids,
        "is_public": is_public,
    }

    return StreamingResponse(
        _stream_upload(tmp_path, file.filename, acl),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
