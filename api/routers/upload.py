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
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import db as authdb
from api.deps import require_uploader, get_current_user
from api.services import index_uploaded_file_streaming

router = APIRouter(prefix="/api/upload", tags=["upload"])
logger = logging.getLogger("hawkins.upload")


class DuplicateCheckRequest(BaseModel):
    filename: str
    # Historical field name is `doc_id` for backward-compatibility with
    # existing frontend callers — its VALUE is a content SHA-1 (first 16
    # hex chars). Kept as-is so nothing external to this module has to
    # change name; the batch endpoint below uses the clearer name
    # `content_sha1`.
    doc_id: str


class DuplicateCheckBatchItem(BaseModel):
    filename: str
    content_sha1: str = Field(..., min_length=1)


class DuplicateCheckBatchRequest(BaseModel):
    items: List[DuplicateCheckBatchItem]


def _classify(content_sha1: str, filename: str, by_hash: dict, by_name: dict) -> dict:
    """
    Shared classification used by both the single and batch endpoints.

    Rules (in order):
      1. content match, regardless of filename → exact_duplicate
      2. filename match (case-insensitive) with a DIFFERENT content hash
         → same_name_conflict
      3. otherwise → ok

    by_hash / by_name are pre-fetched, ACL-scoped lookup dicts:
        by_hash[content_sha1] -> [row, ...]   (multiple rows possible; see
                                                point 7 in the plan — the
                                                archive already contains
                                                content-identical files)
        by_name[filename.lower()] -> [row, ...]
    """
    hits = by_hash.get(content_sha1) or []
    if hits:
        # Multiple accessible rows can legitimately share a content hash
        # (existing corpus contains such pairs). Return a stable choice —
        # the earliest-created row wins so repeated checks give the same
        # reference — without deleting or merging the others.
        chosen = min(hits, key=lambda r: r.get("created_at") or "")
        return {"verdict": "exact_duplicate", "existing": _slim(chosen)}

    name_hits = [
        r for r in (by_name.get(filename.lower()) or [])
        if (r.get("content_sha1") or "") != content_sha1
    ]
    if name_hits:
        chosen = min(name_hits, key=lambda r: r.get("created_at") or "")
        return {"verdict": "same_name_conflict", "existing": _slim(chosen)}

    return {"verdict": "ok", "existing": None}


@router.post("/check")
def check_duplicate(body: DuplicateCheckRequest, user: dict = Depends(get_current_user)):
    """
    Pre-upload duplicate check for a single file.

    Exact-duplicate detection is based on content_sha1 (the canonical
    server-computed SHA-1 of the file's bytes, truncated to 16 hex chars).
    Filename is only a secondary signal — a same-name/different-content
    upload returns `same_name_conflict`. A rename of the same content
    still returns `exact_duplicate`.

    ACL: scoped to allowed_doc_ids(user). A user cannot learn that an
    inaccessible document exists by uploading identical content — hits
    against documents outside the user's allowed set are filtered out
    before classification.
    """
    allowed = authdb.allowed_doc_ids(user)
    hash_rows = authdb.find_files_by_content_sha1([body.doc_id], allowed)
    name_rows = authdb.find_files_by_filenames([body.filename], allowed)

    by_hash = {body.doc_id: hash_rows} if hash_rows else {}
    by_name = {body.filename.lower(): name_rows} if name_rows else {}

    return _classify(body.doc_id, body.filename, by_hash, by_name)


@router.post("/check-batch")
def check_duplicate_batch(
    body: DuplicateCheckBatchRequest, user: dict = Depends(get_current_user)
):
    """
    Batch pre-upload duplicate check. Same classification rules and ACL
    scoping as /check, run against N items in a single request instead of
    forcing the frontend to issue N sequential round-trips.

    Two indexed SQL queries total (one on content_sha1, one on lower(source)),
    regardless of N. Result order matches input order.
    """
    if not body.items:
        return {"results": []}

    allowed = authdb.allowed_doc_ids(user)

    # De-duplicate the lookup keys so we don't pass the same hash/name
    # twice into the SQL IN () clause. The classification below is still
    # per-item.
    hashes = list({item.content_sha1 for item in body.items})
    names = list({item.filename for item in body.items})

    hash_rows = authdb.find_files_by_content_sha1(hashes, allowed)
    name_rows = authdb.find_files_by_filenames(names, allowed)

    by_hash: dict = {}
    for r in hash_rows:
        by_hash.setdefault(r["content_sha1"], []).append(r)

    by_name: dict = {}
    for r in name_rows:
        by_name.setdefault(r["source"].lower(), []).append(r)

    results = [
        _classify(item.content_sha1, item.filename, by_hash, by_name)
        for item in body.items
    ]
    return {"results": results}


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
