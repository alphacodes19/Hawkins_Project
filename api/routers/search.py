from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_current_user
from api.schemas import SearchRequest
from api.services import delete_session_history_entry, load_session_history, upsert_session_query
from auth import db as authdb

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("")
def search(body: SearchRequest, user: dict = Depends(get_current_user)):
    """
    Runs the hybrid retrieval pipeline and returns ranked documents.
    Equivalent to app.py's `retrieve_documents(...)` call in the results area.
    """
    from retrieval.retriever import retrieve_documents

    allowed = authdb.allowed_doc_ids(user)
    docs, coverage = retrieve_documents(
        body.query,
        top_n_docs=body.top_n_docs,
        allowed_doc_ids=allowed,
    )

    # Persist to per-user session history, same as app.py did on every search.
    # The frontend owns session_id/session_start (one per browser tab session)
    # and sends it back so multiple queries group into one session entry.
    return {"docs": docs, "coverage": coverage}


@router.post("/history/log")
def log_query(payload: dict, user: dict = Depends(get_current_user)):
    """
    payload: {session_id, query, session_start} — session_start is an ISO string
    generated once per browser session on the frontend.
    """
    session_start = datetime.fromisoformat(payload["session_start"])
    upsert_session_query(user["username"], payload["session_id"], payload["query"], session_start)
    return {"ok": True}


@router.get("/history")
def get_history(user: dict = Depends(get_current_user)):
    return load_session_history(user["username"])


@router.delete("/history/{entry_id}")
def delete_history_entry(entry_id: int, user: dict = Depends(get_current_user)):
    """
    Permanently deletes one query from the caller's own search history.
    Ownership is enforced inside delete_session_history_entry (the DELETE's
    WHERE clause includes username) — not decided here — so there is no
    way to pass another user's entry_id and have it succeed. Deliberately
    NOT admin-overridable: search history is private, and nothing in the
    existing security model grants admins visibility into another user's
    searches, so this doesn't invent that access either.
    """
    deleted = delete_session_history_entry(entry_id, user["username"])
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "History entry not found")
    return {"ok": True}


@router.post("/resolve-sources")
def resolve_sources(payload: dict, user: dict = Depends(get_current_user)):
    """
    Given a list of filenames (from coverage.keyword_sources — files that
    matched the keyword but ranked below the top N shown), look up each
    one's doc_id + file existence so the UI can offer a Download button.

    Restores app.py's "Show all N files containing this keyword" expander,
    which read straight from ChromaDB by source name (app.py ~line 888-901).
    ACL-checked the same way everything else is — a keyword match doesn't
    bypass department restrictions.
    """
    from api.services import get_chroma_collection

    sources = payload.get("sources", [])
    allowed = authdb.allowed_doc_ids(user)
    collection = get_chroma_collection()

    out = []
    for src in sources:
        res = collection.get(where={"source": src}, limit=1, include=["metadatas"])
        metas = res.get("metadatas") or []
        if not metas:
            out.append({"source": src, "doc_id": None, "available": False})
            continue
        doc_id = metas[0].get("doc_id")
        available = doc_id is not None and (allowed is None or doc_id in allowed)
        out.append({"source": src, "doc_id": doc_id if available else None, "available": available})

    return out
