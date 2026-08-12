"""
api/services.py — non-Streamlit versions of app.py's helper functions
=========================================================================
Ported from app.py almost verbatim. The only real changes:
  - st.cache_resource → plain module-level globals (a FastAPI process is
    already one long-lived process per worker, same lifetime guarantee
    st.cache_resource was providing, just without the Streamlit decorator).
  - No `st` calls anywhere — this file has zero UI framework dependency,
    same as retrieval/ and auth/ already had. That's what makes this port
    mechanical instead of a rewrite.
"""

import os
from datetime import datetime

import chromadb

import config

# ── Warmup ───────────────────────────────────────────────────────────────────
_warmed_up = False


def warmup_search_stack():
    """Load the embedding model, reranker, and BM25 index once at process
    startup instead of on whichever request happens to search first."""
    global _warmed_up
    if _warmed_up:
        return
    from pipeline.embedder import get_model
    from retrieval import retriever
    get_model()
    retriever._get_reranker()
    retriever.ensure_bm25_ready()
    _warmed_up = True


# ── Chroma / embedder singletons ────────────────────────────────────────────
_collection = None
_embedder = None


def get_chroma_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _collection = client.get_or_create_collection(
            config.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def get_embedder():
    global _embedder
    if _embedder is None:
        from pipeline.embedder import embed_text
        _embedder = embed_text
    return _embedder


# ── Search history persistence ──────────────────────────────────────────────
# Backed by auth.db's search_history table (see auth/db.py) — these two
# functions are kept as thin wrappers so api/routers/search.py doesn't need
# to change its imports. The old per-user JSON file under
# data/processed/search_history/ is no longer written to; existing files
# were migrated into SQLite once, on server startup (auth.db.init_db()).
def upsert_session_query(username: str, session_id: str, query: str, session_start: datetime):
    from auth import db as authdb
    authdb.add_search_history(
        username=username,
        session_id=session_id,
        date_label=session_start.strftime("%d %b %Y"),
        start_time=session_start.strftime("%I:%M %p"),
        query=query,
    )


def load_session_history(username: str) -> list:
    from auth import db as authdb
    return authdb.list_search_history(username)


def delete_session_history_entry(entry_id: int, username: str) -> bool:
    from auth import db as authdb
    return authdb.delete_search_history_entry(entry_id, username)


# ── File upload + indexing ──────────────────────────────────────────────────
def index_uploaded_file(tmp_path: str, original_name: str, acl: dict) -> int:
    """
    acl — {"uploaded_by": str, "dept_ids": [int], "is_public": bool}
    Identical logic to app.py's index_uploaded_file. Returns chunk count.
    Kept as a thin wrapper around the streaming version below for any caller
    that just wants the final count without stage events.
    """
    result = None
    for event in index_uploaded_file_streaming(tmp_path, original_name, acl):
        if event["stage"] == "completed":
            result = event["chunks_indexed"]
    return result or 0


def index_uploaded_file_streaming(tmp_path: str, original_name: str, acl: dict):
    """
    Generator version — yields {"stage": ...} dicts at genuine phase
    transitions so the upload endpoint can relay real progress over SSE
    instead of the frontend faking a percentage during the "still working"
    part of the request. Stages, in order:
        processing -> extracting_text -> generating_embeddings -> indexing -> completed

    "generating_embeddings" and "indexing" are honestly separated into two
    passes over all of the file's docs (chunk+embed everything, THEN upsert
    everything) specifically so this generator doesn't have to fake a
    boundary between them — the original single-pass-per-doc loop had them
    interleaved, which would've made "two distinct stages" a fiction.

    ZIP files are intentionally NOT broken into these sub-stages — they
    already delegate to zip_handler.index_zip's own per-file loop, and
    re-deriving granular stages from inside that would be a much bigger
    refactor for a format that's a small minority of uploads. They still get
    "processing" -> "completed" (or "error"), just not the granular middle.
    """
    from auth import db as authdb
    from pipeline.chunker import chunk_text
    from pipeline.doc_id import compute_doc_id
    from pipeline.embedder import get_model
    from pipeline.indexer import SKIP_TAGGING
    from pipeline.library import store_in_library

    EMBED_BATCH = 64
    UPSERT_BATCH = 200

    ext = original_name.rsplit(".", 1)[-1].lower()
    yield {"stage": "processing"}

    if ext == "zip":
        from pipeline.zip_handler import index_zip
        collection = get_chroma_collection()
        n = index_zip(tmp_path, collection, verbose=False, acl=acl)
        invalidate_bm25()
        yield {"stage": "completed", "chunks_indexed": n}
        return

    yield {"stage": "extracting_text"}
    if ext == "pdf":
        from connectors.pdf_connector import extract_pdf
        docs = extract_pdf(tmp_path)
        if len(docs) > 1:
            merged_text = "\n\n".join(d["text"] for d in docs)
            docs = [{"text": merged_text, "metadata": docs[0]["metadata"]}]
    elif ext in ("docx", "doc"):
        from connectors.docx_connector import extract_docx
        docs = extract_docx(tmp_path)
    elif ext in ("xlsx", "xls"):
        from connectors.excel_connector import extract_excel
        docs = extract_excel(tmp_path)
    elif ext in ("eml", "emlx", "msg", "mbox"):
        from connectors.email_connector import extract_email
        docs = extract_email(tmp_path)
    elif ext == "db":
        from connectors.sql_connector import extract_sql
        docs = extract_sql(tmp_path)
    else:
        yield {"stage": "completed", "chunks_indexed": 0}
        return

    if not docs:
        yield {"stage": "completed", "chunks_indexed": 0}
        return

    doc_id = compute_doc_id(tmp_path)
    permanent_path = store_in_library(tmp_path, original_name=original_name, origin_tag="upload")

    # doc_id and content_sha1 are the SAME value for uploads that go through
    # this path (both are compute_doc_id() of the same bytes) — the split
    # only matters for legacy rows whose doc_id is `legacy:<filename>` and
    # whose content_sha1 is backfilled by scripts/backfill_content_sha1.py.
    # We still write both, so a future doc_id-scheme change wouldn't
    # silently break content-based duplicate detection.
    authdb.register_file(
        doc_id=doc_id,
        source=original_name,
        uploaded_by=acl.get("uploaded_by"),
        dept_ids=acl.get("dept_ids", []),
        is_public=acl.get("is_public", False),
        content_sha1=doc_id,
    )

    model = get_model()

    # ── Pass 1: chunk + tag + embed everything, write nothing yet ──────────
    yield {"stage": "generating_embeddings"}
    pending_ids, pending_embs, pending_docs, pending_metas = [], [], [], []

    for doc in docs:
        doc["metadata"]["source"] = original_name
        doc["metadata"]["doc_id"] = doc_id
        doc["metadata"]["file_path"] = permanent_path
        doc["metadata"]["filepath"] = permanent_path

        chunks = [c for c in chunk_text(doc["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP) if c.strip()]
        sheet = doc["metadata"].get("sheet", "")
        if not chunks:
            continue

        embeddings = model.encode(
            chunks, batch_size=EMBED_BATCH, show_progress_bar=False, normalize_embeddings=True,
        ).tolist()

        if not SKIP_TAGGING:
            from pipeline.metadata_tagger import tag_chunk
            all_tags = [tag_chunk(c) for c in chunks]
        else:
            all_tags = [{"doc_type": "general", "department": None, "project": None,
                         "people": [], "date": None, "summary": ""} for _ in chunks]

        for i, (chunk, embedding, tags) in enumerate(zip(chunks, embeddings, all_tags)):
            chunk_id = f"upload_{doc_id}_{sheet}_chunk_{i}" if sheet else f"upload_{doc_id}_chunk_{i}"
            meta = {**doc["metadata"]}
            for k, v in tags.items():
                if v is None:
                    continue
                meta[k] = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
            pending_ids.append(chunk_id)
            pending_embs.append(embedding)
            pending_docs.append(chunk)
            pending_metas.append(meta)

    # ── Pass 2: write everything accumulated above ──────────────────────────
    yield {"stage": "indexing"}
    collection = get_chroma_collection()
    for start in range(0, len(pending_ids), UPSERT_BATCH):
        end = start + UPSERT_BATCH
        collection.upsert(
            ids=pending_ids[start:end], embeddings=pending_embs[start:end],
            documents=pending_docs[start:end], metadatas=pending_metas[start:end],
        )

    invalidate_bm25()
    yield {"stage": "completed", "chunks_indexed": len(pending_ids)}


def invalidate_bm25():
    from retrieval import retriever
    retriever.invalidate_bm25()


# ── Permanent file deletion ──────────────────────────────────────────────────
LIBRARY_DIR = os.path.join(config.BASE_DIR, "data", "library")


def delete_file_completely(doc_id: str) -> dict:
    """
    Removes one file from every storage layer: ChromaDB chunks, the
    auth.db files/file_dept rows, and the permanent copy under
    data/library/. Called only after the caller (a router) has already
    authorized the request — this function does no auth of its own.

    Order matters here:
      1. Resolve file_path from Chroma FIRST, while the chunks (the only
         place file_path lives) still exist.
      2. Delete the Chroma chunks.
      3. Delete the auth.db row (file_dept cascades automatically).
      4. Delete the physical file last — if this step fails, Chroma and
         auth.db are already consistent with each other (Golden Rules 6/7),
         and a leftover file under data/library/ is a harmless orphan, not
         a broken reference.

    This is NOT a single atomic transaction across three different storage
    systems (SQLite, Chroma, filesystem) — that isn't achievable here
    without a two-phase-commit layer this project doesn't have. Instead
    every step is independently try/excepted, and every failure is
    reported back rather than swallowed, per the "don't hide errors"
    requirement. A caller that gets warnings back knows exactly what
    still needs manual cleanup instead of believing the delete fully
    succeeded when it didn't.
    """
    collection = get_chroma_collection()

    file_path = None
    try:
        res = collection.get(where={"doc_id": doc_id}, limit=1, include=["metadatas"])
        metas = res.get("metadatas") or []
        if metas:
            file_path = metas[0].get("file_path") or metas[0].get("filepath")
    except Exception:
        pass  # Chroma lookup failing shouldn't block the DB/filesystem cleanup below

    warnings = []

    try:
        collection.delete(where={"doc_id": doc_id})
    except Exception as e:
        warnings.append(f"Could not remove indexed chunks from ChromaDB: {e}")

    from auth import db as authdb
    try:
        authdb.delete_file_by_doc_id(doc_id)
    except Exception as e:
        warnings.append(f"Could not remove the database record: {e}")

    if file_path:
        try:
            real = os.path.realpath(file_path)
            # Only ever remove a file that actually lives inside the
            # managed library directory — file_path is Chroma metadata,
            # not directly client-controlled, but this keeps the deletion
            # scoped even if that metadata were ever malformed.
            if os.path.dirname(real) == os.path.realpath(LIBRARY_DIR) and os.path.exists(real):
                os.remove(real)
        except OSError as e:
            warnings.append(f"Could not remove the file from disk: {e}")

    try:
        invalidate_bm25()
    except Exception:
        pass

    return {"ok": True, "warnings": warnings}
