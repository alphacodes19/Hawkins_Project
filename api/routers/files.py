"""
api/routers/files.py — serve indexed files by doc_id
=========================================================
Deliberately takes doc_id, never a filesystem path. A raw ?path= query param
coming from the browser would be a path-traversal vector (read any file the
server process can see); doc_id is opaque, is exactly what allowed_doc_ids()
already gates on, and is re-checked here even though the frontend only shows
View/Download buttons for documents retrieve_documents() already filtered —
never trust the client to have honestly enforced a permission check.
"""

import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response

from api.deps import get_current_user, require_uploader
from api.services import get_chroma_collection
from auth import db as authdb

router = APIRouter(prefix="/api/files", tags=["files"])


def _resolve_file_path(doc_id: str) -> tuple[str, str]:
    """Look up one chunk's metadata for this doc_id to recover file_path +
    the original source filename (chunk metadata is the only place file_path
    lives — auth.db's files table only tracks doc_id/source/ACL)."""
    collection = get_chroma_collection()
    res = collection.get(where={"doc_id": doc_id}, limit=1, include=["metadatas"])
    metas = res.get("metadatas") or []
    if not metas:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    meta = metas[0]
    file_path = meta.get("file_path") or meta.get("filepath")
    source = meta.get("source", os.path.basename(file_path or "file"))
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Original file is no longer available")
    return file_path, source


def _check_access(doc_id: str, user: dict):
    allowed = authdb.allowed_doc_ids(user)
    if allowed is not None and doc_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this document")


@router.get("/download")
def download_file(doc_id: str, user: dict = Depends(get_current_user)):
    _check_access(doc_id, user)
    file_path, source = _resolve_file_path(doc_id)
    return FileResponse(file_path, filename=source, media_type="application/octet-stream")


@router.get("/email")
def email_preview(doc_id: str, user: dict = Depends(get_current_user)):
    """
    Parses an .eml/.msg/.mbox file into structured fields for the email card
    UI. Ported from app.py's View-panel + results-card email logic (both did
    near-identical parsing; this is the one place it lives now).

    attachments (name/content_type/size) weren't tracked anywhere in the
    Streamlit version — added here since it's cheap once we're already
    walking MIME parts. index is positional within this same walk order, and
    /email-attachment below re-walks with the identical logic to find the
    same part by that index — the two MUST stay in lockstep, which is why
    the attachment-collecting condition here is factored into
    _is_attachment_part() and reused by both endpoints rather than
    duplicated with any risk of drifting out of sync.
    """
    _check_access(doc_id, user)
    file_path, source = _resolve_file_path(doc_id)
    msg = _parse_email(file_path, source)

    subject = str(msg.get("Subject", "")).strip()
    sender = str(msg.get("From", "")).strip()
    to = str(msg.get("To", "")).strip()
    cc = str(msg.get("Cc", "")).strip()
    date = str(msg.get("Date", "")).strip()

    body = ""
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            if _is_attachment_part(part):
                payload = part.get_payload(decode=True)
                attachments.append({
                    "filename":     part.get_filename() or f"attachment-{len(attachments) + 1}",
                    "content_type": part.get_content_type(),
                    "size":         len(payload) if payload else 0,
                })
                continue
            if not body and part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True)
                    body = payload.decode("utf-8", errors="replace") if payload else ""
    else:
        try:
            body = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True)
            body = payload.decode("utf-8", errors="replace") if payload else ""

    # Strip MIME boundaries / encoding noise, same heuristic as app.py's version.
    clean_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if clean_lines and clean_lines[-1] != "":
                clean_lines.append("")
            continue
        if re.match(r"^(--|Content-|charset=|boundary=|[A-Za-z0-9+/]{60,}={0,2})$", stripped):
            continue
        clean_lines.append(line)
    clean_body = "\n".join(clean_lines).strip()

    return {
        "subject": subject,
        "from": sender,
        "to": to,
        "cc": cc,
        "date": date,
        "body": clean_body or "No readable text content found in this email.",
        "has_attachments": len(attachments) > 0,
        "attachments": attachments,
    }


def _parse_email(file_path: str, source: str):
    import email as email_lib
    from email import policy as email_policy

    ext = source.rsplit(".", 1)[-1].lower() if "." in source else ""
    if ext not in ("eml", "emlx", "msg", "mbox"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not an email file")

    try:
        with open(file_path, "rb") as fh:
            return email_lib.message_from_binary_file(fh, policy=email_policy.default)
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Could not parse email: {e}")


def _is_attachment_part(part) -> bool:
    disposition = str(part.get("Content-Disposition", ""))
    return bool(
        disposition.startswith("attachment")
        or (part.get_filename() and part.get_content_type() != "text/plain")
    )


def _resolve_attachment(doc_id: str, index: int, user: dict) -> tuple[bytes, str, str]:
    """Returns (raw_bytes, filename, content_type) for the attachment at
    `index`, using the exact same walk order _is_attachment_part() produces
    in email_preview() above, so an index handed back by that endpoint
    always resolves to the same part here."""
    _check_access(doc_id, user)
    file_path, source = _resolve_file_path(doc_id)
    msg = _parse_email(file_path, source)

    if not msg.is_multipart():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This email has no attachments")

    seen = 0
    for part in msg.walk():
        if not _is_attachment_part(part):
            continue
        if seen == index:
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename() or f"attachment-{index + 1}"
            content_type = part.get_content_type() or "application/octet-stream"
            return payload, filename, content_type
        seen += 1

    raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found at that index")


@router.get("/view")
def view_file(doc_id: str, user: dict = Depends(get_current_user)):
    """
    Same file as /download but rendered inline (PDF in an iframe, image in an
    <img>, etc.) instead of forcing a browser download.

    FileResponse defaults to `Content-Disposition: attachment` the moment you
    pass `filename=`, regardless of media_type — that default is what made
    View behave like Download. content_disposition_type="inline" is the fix.
    """
    _check_access(doc_id, user)
    file_path, source = _resolve_file_path(doc_id)
    return FileResponse(file_path, filename=source, content_disposition_type="inline")


@router.get("/email-attachment/view")
def view_email_attachment(doc_id: str, index: int, user: dict = Depends(get_current_user)):
    """Inline (same content_disposition_type="inline" reasoning as /view
    above) — lets FilePreviewBody preview a supported attachment type
    (PDF/image/text) exactly the way it previews a normal indexed file,
    just pointed at this URL instead of /view?doc_id=."""
    payload, filename, content_type = _resolve_attachment(doc_id, index, user)
    return Response(content=payload, media_type=content_type,
                     headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("/email-attachment/download")
def download_email_attachment(doc_id: str, index: int, user: dict = Depends(get_current_user)):
    payload, filename, content_type = _resolve_attachment(doc_id, index, user)
    return Response(content=payload, media_type="application/octet-stream",
                     headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Uploader's own files (Feature 8) ─────────────────────────────────────────
@router.get("/mine")
def list_recent_uploads(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "newest",
    limit: Optional[int] = None,
    user: dict = Depends(require_uploader),
):
    """
    "Recently uploaded files" for the uploader/admin-only view. Scoped
    through the same allowed_doc_ids() ACL as every other listing in the
    app — an uploader sees what they're already allowed to see (their own
    uploads, public files, their department's files), never the whole
    archive. `can_delete` tells the frontend whether to show a delete
    control for that row, but the actual DELETE endpoint below re-checks
    ownership independently — this flag is a UI convenience only, not the
    authorization boundary.
    """
    allowed = authdb.allowed_doc_ids(user)
    rows = authdb.list_files(date_from=date_from, date_to=date_to, sort=sort, limit=limit)
    if allowed is not None:
        rows = [r for r in rows if r["doc_id"] in allowed]
    is_admin = user.get("role") == "admin"
    for r in rows:
        r["can_delete"] = is_admin or r.get("uploaded_by") == user.get("username")
    return rows


@router.delete("")
def delete_file(doc_id: str, user: dict = Depends(require_uploader)):
    """
    An uploader may delete files THEY uploaded; an admin may delete any
    file. This check is independent of, and re-derived from, the current
    auth.db state on every call — never trusting a `can_delete` flag the
    client might echo back. Deletion itself is the same
    delete_file_completely() used by the admin panel's permanent delete,
    so the storage-consistency guarantees are identical either way.
    """
    existing = next((f for f in authdb.list_files() if f["doc_id"] == doc_id), None)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    is_admin = user.get("role") == "admin"
    is_owner = existing.get("uploaded_by") == user.get("username")
    if not (is_admin or is_owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete files you uploaded")

    from api.services import delete_file_completely
    result = delete_file_completely(doc_id)

    authdb.record_audit(
        user["username"], "FILE_DELETED", "file", target_id=doc_id,
        description=f"Deleted file '{existing['source']}'"
                     + (" (own upload)" if is_owner and not is_admin else ""),
        before={"source": existing["source"], "uploaded_by": existing.get("uploaded_by")},
    )
    return result
