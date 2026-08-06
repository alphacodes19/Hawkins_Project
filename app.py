import streamlit as st
import os
import sys
import tempfile
import chromadb

# ── CRITICAL: Set working directory to project root so config.py resolves correctly
APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
# ── Logo (base64 PNG, loaded once from static/ to keep source readable) ──────
_LOGO_B64 = open(os.path.join(APP_DIR, "static", "hawkins_logo_b64.txt")).read()


import config
from retrieval.generator import ANSWER_PROMPT
from auth import db as authdb
from auth import ui as authui
from eval.faithfulness_check import check_faithfulness

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Hawkins Data Archive",
    page_icon=None,
    layout="wide"
)

# Hide Streamlit's default multipage navigation from the sidebar
# We build our own clean navigation below
st.markdown("""
<style>
    /* Hide the default Streamlit page nav list in sidebar */
    [data-testid="stSidebarNav"] {display: none;}
    /* Clean up sidebar top padding */
    [data-testid="stSidebar"] > div:first-child {padding-top: 1rem;}
    /* Style sidebar links */
    [data-testid="stSidebar"] a {text-decoration: none;}


</style>
""", unsafe_allow_html=True)

# ── Login gate ────────────────────────────────────────────────────────────────
# Nothing below this line runs for an anonymous visitor — require_login() calls
# st.stop() after rendering the sign-in form.
USER = authui.require_login()

# The set of doc_ids this user may see. None means admin (no filter at all).
# Recomputed on every script run, which is every Streamlit interaction, so a
# permission change by the admin takes effect on the user's very next query
# rather than after they sign out and back in.
ALLOWED_DOC_IDS = authdb.allowed_doc_ids(USER)

# ── Warmup ────────────────────────────────────────────────────────────────────
# Loads the embedding model, the reranker, and the BM25 index once per server
# process instead of paying for all three during whichever user happens to
# submit the first search. st.cache_resource is what makes this safe to call
# unconditionally on every script rerun (every widget interaction reruns this
# whole file) — without it, ensure_bm25_ready()/get_model()/_get_reranker()
# would just no-op after the first call anyway (they're each individually
# lazy-guarded), but caching makes the intent explicit and lets Streamlit
# report it as a resource in its cache stats.
#
# Note on what this actually buys you: Streamlit doesn't run any app code
# before a request arrives, so this isn't "warmed before the server accepts
# traffic" in the literal sense — it's "warmed by whoever loads this page
# first" rather than "warmed by whoever searches first." That's still a real
# win (a page-load spinner reads very differently than a search that silently
# hangs), just not a free lunch. It also only fires on this page — the Admin
# page is a separate script and doesn't import these modules, so a session
# that lands there first won't trigger it.
@st.cache_resource(show_spinner="Warming up search…")
def _warmup_search_stack():
    from pipeline.embedder import get_model
    from retrieval import retriever
    get_model()                    # loads BGE-M3
    retriever._get_reranker()      # loads the cross-encoder
    retriever.ensure_bm25_ready()  # builds BM25 index + metadata cache
    return True

_warmup_search_stack()

# ── Helpers ───────────────────────────────────────────────────────────────────

# ── Search history persistence ────────────────────────────────────────────────
import json as _json
from datetime import datetime as _dt

def _history_path(username):
    """Per-user session history file stored in data/processed/search_history/"""
    hist_dir = os.path.join(config.BASE_DIR, "data", "processed", "search_history")
    os.makedirs(hist_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in username)
    return os.path.join(hist_dir, f"{safe}_sessions.json")

def load_session_history(username):
    """Load all saved sessions for a user. Newest first."""
    path = _history_path(username)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
    except Exception:
        pass
    return []

def upsert_session_query(username, session_id, query, session_start):
    """
    Add a query to the current session in the history file.
    Called after EVERY search so history is always persisted to disk —
    no data is lost if the app crashes or the user closes without signing out.

    If the session_id already exists in the file, the query is appended to it.
    If not, a new session entry is created.
    Keeps last 50 sessions.
    """
    path     = _history_path(username)
    sessions = load_session_history(username)

    # Find existing session entry for this session_id
    existing = next((s for s in sessions if s.get("session_id") == session_id), None)

    if existing:
        # Append query to existing session if not already there
        if query not in existing["queries"]:
            existing["queries"].append(query)
    else:
        # Create new session entry
        sessions.insert(0, {
            "session_id": session_id,
            "date_label": session_start.strftime("%d %b %Y"),
            "start_time": session_start.strftime("%I:%M %p"),
            "queries":    [query],
        })
        sessions = sessions[:50]

    try:
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(sessions, f, indent=2)
    except Exception as e:
        pass  # never block the search over a history write failure

def save_session_to_history(username, queries):
    """Legacy — kept for sign-out call compatibility. No-op now."""
    pass

@st.cache_resource
def get_chroma_collection():
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return client.get_or_create_collection(
        config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )


@st.cache_resource
def get_embedder():
    from pipeline.embedder import embed_text
    return embed_text


def index_uploaded_file(tmp_path, original_name, acl):
    """
    acl — {"uploaded_by": str, "dept_ids": [int], "is_public": bool}
          Captured from the upload form before indexing starts, so a file is
          never queryable for even one request before its permissions exist.

    Uses batch embedding and batch upsert — same approach as the fast indexer.
    Metadata tagging is skipped (matches SKIP_TAGGING in indexer.py) so uploads
    finish in seconds instead of minutes.
    """
    from pipeline.chunker import chunk_text
    from pipeline.embedder import get_model
    from pipeline.doc_id import compute_doc_id
    from pipeline.library import store_in_library
    from pipeline.indexer import SKIP_TAGGING

    EMBED_BATCH  = 64
    UPSERT_BATCH = 200

    ext = original_name.rsplit(".", 1)[-1].lower()

    if ext == "zip":
        from pipeline.zip_handler import index_zip
        collection = get_chroma_collection()
        n = index_zip(tmp_path, collection, verbose=False, acl=acl)
        _invalidate_bm25()
        return n

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
        return 0

    if not docs:
        return 0

    doc_id         = compute_doc_id(tmp_path)
    permanent_path = store_in_library(tmp_path, original_name=original_name,
                                      origin_tag="upload")

    authdb.register_file(
        doc_id      = doc_id,
        source      = original_name,
        uploaded_by = acl.get("uploaded_by"),
        dept_ids    = acl.get("dept_ids", []),
        is_public   = acl.get("is_public", False),
    )

    collection = get_chroma_collection()
    model      = get_model()
    total      = 0

    for doc in docs:
        doc["metadata"]["source"]    = original_name
        doc["metadata"]["doc_id"]    = doc_id
        doc["metadata"]["file_path"] = permanent_path
        doc["metadata"]["filepath"]  = permanent_path

        chunks = [c for c in chunk_text(
            doc["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP
        ) if c.strip()]
        sheet = doc["metadata"].get("sheet", "")

        if not chunks:
            continue

        # ── Batch embed all chunks at once ────────────────────────────────
        embeddings = model.encode(
            chunks,
            batch_size=EMBED_BATCH,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

        # ── Optionally tag (skipped when SKIP_TAGGING=True) ───────────────
        if not SKIP_TAGGING:
            from pipeline.metadata_tagger import tag_chunk
            all_tags = [tag_chunk(c) for c in chunks]
        else:
            all_tags = [{"doc_type": "general", "department": None,
                         "project": None, "people": [], "date": None,
                         "summary": ""} for _ in chunks]

        # ── Build IDs + metadatas ─────────────────────────────────────────
        ids, embs, docs_list, metas = [], [], [], []
        for i, (chunk, embedding, tags) in enumerate(
                zip(chunks, embeddings, all_tags)):
            chunk_id = (
                f"upload_{doc_id}_{sheet}_chunk_{i}" if sheet
                else f"upload_{doc_id}_chunk_{i}"
            )
            meta = {**doc["metadata"]}
            for k, v in tags.items():
                if v is None:
                    continue
                meta[k] = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)

            ids.append(chunk_id)
            embs.append(embedding)
            docs_list.append(chunk)
            metas.append(meta)

        # ── Batch upsert ──────────────────────────────────────────────────
        for start in range(0, len(ids), UPSERT_BATCH):
            end = start + UPSERT_BATCH
            collection.upsert(
                ids=ids[start:end],
                embeddings=embs[start:end],
                documents=docs_list[start:end],
                metadatas=metas[start:end],
            )
        total += len(chunks)

    _invalidate_bm25()
    return total


def _invalidate_bm25():
    """Force BM25 rebuild so newly indexed docs are immediately keyword-searchable."""
    from retrieval import retriever
    retriever.invalidate_bm25()


def _run_faithfulness_check(answer, chunks):
    """
    Runs the Level 1 (citation audit) + Level 2 (n-gram overlap) faithfulness
    checks on a generated answer. Deliberately run_llm_check=False here —
    Level 3 makes its own Ollama call, which would stack another 15-25s CPU
    inference on top of the answer generation we already just waited on.
    Levels 1+2 are pure Python/regex, effectively free by comparison.

    Returns the check_faithfulness() result dict, or None if the check
    itself errors (never let a faithfulness-check bug break the answer
    that's already been generated and shown).
    """
    try:
        return check_faithfulness(answer=answer, chunks=chunks, run_llm_check=False)
    except Exception:
        return None


def _render_faithfulness_warning(result):
    """
    Shows a warning only when the check found something — stays silent
    when the answer looks grounded, per check_faithfulness()'s own
    recommended usage (don't clutter the UI on the common/good case).
    """
    if not result or result.get("is_faithful", True):
        return
    issues = result.get("issues") or ["Some claims in this answer may not be fully supported by the retrieved documents."]
    st.warning("⚠ " + "  \n".join(issues))


def stream_answer(question, docs=None):
    """
    Stream the LLaMA response token by token.
    Uses pre-retrieved docs from Phase 1 when available (better quality).
    Falls back to original retrieve() if docs not provided.
    Yields text tokens. Stores chunks in session state for Sources expander.
    """
    import ollama

    # Build chunks list for context + Sources expander
    if docs:
        # Extract best N chunks per document from already-retrieved docs.
        # Uses config.ANSWER_TOP_DOCS and config.ANSWER_CHUNKS_PER_DOC so the
        # values are set in one place and stay in sync with num_ctx.
        # On the 128GB server: 8 docs × 2 chunks × ~300 words ≈ 6,400 tokens,
        # well within the 16,384 token context window.
        chunks = []
        for d in docs[:config.ANSWER_TOP_DOCS]:
            # Sort matched chunks by score, take the top N
            sorted_mc = sorted(d["matched_chunks"], key=lambda x: x["score"], reverse=True)
            for mc in sorted_mc[:config.ANSWER_CHUNKS_PER_DOC]:
                chunks.append({
                    "text":        mc["text"],
                    "source":      d["source"],
                    "source_type": d.get("source_type", ""),
                    "doc_type":    d.get("doc_type", ""),
                    "page":        mc.get("page", ""),
                    "score":       round(mc["score"], 3),
                })
    else:
        # Fallback: original pure-vector retrieve.
        # Must carry the ACL too — otherwise the panel above correctly hides a
        # restricted file while the answer below quotes it and cites it by name.
        from retrieval.retriever import retrieve
        chunks = retrieve(question, allowed_doc_ids=ALLOWED_DOC_IDS)

    st.session_state["last_chunks"] = chunks

    if not chunks:
        yield "I could not find this information in the available documents."
        return

    context_parts = []
    for c in chunks:
        label = c["source"]
        if c.get("page"):        label += f" | page {c['page']}"
        if c.get("source_type"): label += f" | {c['source_type']}"
        context_parts.append(f"[{label}]\n{c['text']}")

    context = "\n\n".join(context_parts)

    prompt = ANSWER_PROMPT.format(context=context, question=question)

    stream = ollama.chat(
        model=config.OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={
            "num_ctx":     config.OLLAMA_NUM_CTX,
            "num_predict": 512,   # cap output at ~350 words — enough for any
                                  # document answer, prevents runaway generation
            "temperature": 0.1,   # low temperature = faster, more focused output
        },
        stream=True
    )
    for part in stream:
        token = part["message"]["content"]
        if token:
            yield token


def render_document_results(docs, coverage=None, _render_idx=[0]):
    """
    Render ranked document results above the AI answer.
    Shows an honest coverage banner so users know how many files were scanned.
    Provides a 'Show all files containing keyword' toggle for full coverage.
    """
    if not docs and (not coverage or coverage["keyword_file_count"] == 0):
        st.info("No documents found for this query.")
        return

    # ── Coverage banner ───────────────────────────────────────────────────────
    if coverage:
        kfc = coverage["keyword_file_count"]
        kcc = coverage["keyword_chunk_count"]
        if kfc > 0:
            # "you can access", not "in the corpus" — these counts are computed
            # over the user's permitted documents only. Saying "corpus" would
            # imply the true total and quietly reveal that restricted files exist.
            st.info(
                f"Showing **top {len(docs)} documents** ranked by relevance "
                f"· Exact keyword found in **{kfc} files** you can access "
                f"({kcc} total sections)"
            )
        else:
            st.info(
                f"Showing **top {len(docs)} documents** ranked by semantic relevance "
                f"· No exact keyword match found — results are based on meaning"
            )
    else:
        st.markdown(f"**{len(docs)} relevant documents found**")

    # ── Ranked results ────────────────────────────────────────────────────────
    _render_idx[0] += 1
    _render_ns = _render_idx[0]  # unique per call, persists across reruns
    for i, d in enumerate(docs):
        pct       = d.get("relevance_pct", 0)
        file_path = d.get("file_path", "")
        source    = d["source"]
        ext       = source.rsplit(".", 1)[-1].lower() if "." in source else ""

        label = source
        label += f"  —  {pct}% match"

        with st.expander(label, expanded=(i < 3)):
            # ── metadata row ─────────────────────────────────────────────────
            meta_parts = []
            if d.get("department"): meta_parts.append(f"Dept: {d['department']}")
            if d.get("date"):       meta_parts.append(f"Date: {d['date']}")
            if d.get("summary"):    meta_parts.append(d["summary"])
            if meta_parts:
                st.caption("  ·  ".join(meta_parts))

            # ── best matching excerpt ─────────────────────────────────────────
            # Pick the best chunk, but prefer a readable one over a garbled one.
            # Garbled text comes from poor-quality scans where Tesseract reads
            # image noise as characters — it looks like random letter soup.
            # Heuristic: if avg word length > 15 or space ratio < 5%, the chunk
            # is almost certainly garbled OCR. Try another chunk first.
            def _is_garbled(text):
                text = text.strip()
                if not text:
                    return True
                words = text.split()
                if not words:
                    return True
                avg_word_len = sum(len(w) for w in words) / len(words)
                space_ratio  = text.count(" ") / len(text)
                # Also catches reversed/mirrored OCR of numeric tables where
                # words look short but the text is mostly digits and symbols
                alpha_ratio  = sum(1 for c in text if c.isalpha()) / len(text)
                return avg_word_len > 15 or space_ratio < 0.05 or alpha_ratio < 0.40

            chunks_by_score = sorted(d["matched_chunks"], key=lambda x: x["score"], reverse=True)
            best = chunks_by_score[0]
            if _is_garbled(best["text"]) and len(chunks_by_score) > 1:
                # Try to find a readable chunk
                readable = [c for c in chunks_by_score if not _is_garbled(c["text"])]
                if readable:
                    best = readable[0]

            ocr_flag  = best.get("ocr") == "true"
            garbled   = _is_garbled(best["text"])
            page_info = f"  ·  page {best['page']}" if best.get("page") else ""
            ocr_info  = "  (OCR)" if ocr_flag else ""
            st.caption(
                f"{len(d['matched_chunks'])} matching section(s){page_info}{ocr_info}"
            )
            if garbled:
                st.caption(
                    "Text preview unavailable — poor quality scan. "
                    "Use the Download button to read this file."
                )
            else:
                # ── Email: render as a clean card instead of raw text ─────────
                src_type = d.get("source_type", "")
                is_email = src_type in ("email", "email_msg", "email_mbox", "email_emlx") or                            (d["source"].lower().endswith((".eml", ".emlx", ".msg", ".mbox")))

                if is_email:
                    import re as _re
                    import html as _html

                    # ── Parse headers from chunk text ─────────────────────────
                    raw_email = best["text"]
                    raw_norm  = raw_email.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")

                    fields = {}
                    for hdr in ("Subject", "From", "To", "Date"):
                        m = _re.search(
                            hdr + r":\s*(.+?)(?=\s+(?:From|To|Date|Subject|Cc|Bcc)\s*:|$)",
                            raw_norm, _re.IGNORECASE | _re.DOTALL
                        )
                        if m:
                            val = _re.sub(r"\s+", " ", m.group(1).strip())
                            fields[hdr] = val[:150]

                    # ── Header display (always shown) ─────────────────────────
                    if fields.get("Subject"):
                        st.markdown(f"**{fields['Subject']}**")
                    col1, col2 = st.columns(2)
                    with col1:
                        if fields.get("From"):
                            st.caption(f"From: {fields['From'][:80]}")
                    with col2:
                        if fields.get("Date"):
                            st.caption(f"Date: {fields['Date'][:50]}")
                    if fields.get("To"):
                        st.caption(f"To: {fields['To'][:100]}")

                    # ── Scrollable body preview ───────────────────────────────
                    # Reads the actual .eml file from disk (same source as View)
                    # so the content is identical to what View shows.
                    # Fixed-height container with internal scroll on hover.
                    # When cursor is inside the box, mouse wheel scrolls the
                    # email body — not the page. When cursor leaves, normal
                    # page scroll resumes. Implemented via CSS overflow + JS
                    # pointer-events: the outer div captures wheel events only
                    # while hovered (overscroll-behavior: contain).
                    if file_path and os.path.exists(file_path):
                        try:
                            import email as _email_lib
                            from email import policy as _epolicy

                            with open(file_path, "rb") as fh:
                                msg = _email_lib.message_from_binary_file(
                                    fh, policy=_epolicy.default
                                )

                            # Extract body — same logic as View panel
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        try:
                                            body = part.get_content()
                                        except Exception:
                                            body = part.get_payload(
                                                decode=True
                                            ).decode("utf-8", errors="replace")
                                        break
                            else:
                                try:
                                    body = msg.get_content()
                                except Exception:
                                    body = msg.get_payload(
                                        decode=True
                                    ).decode("utf-8", errors="replace")

                            # Clean body — same logic as View panel
                            clean_lines = []
                            for line in body.splitlines():
                                stripped = line.strip()
                                if not stripped:
                                    if clean_lines and clean_lines[-1] != "":
                                        clean_lines.append("")
                                    continue
                                if _re.match(
                                    r"^(--|Content-|charset=|boundary=|"
                                    r"[A-Za-z0-9+/]{60,}={0,2})$",
                                    stripped
                                ):
                                    continue
                                clean_lines.append(line)
                            clean_body = "\n".join(clean_lines).strip()

                            if clean_body:
                                safe_body = _html.escape(clean_body)
                                # Unique ID so JS targets only this specific card
                                scroll_id = f"email_scroll_{_render_ns}_{i}"
                                st.markdown(
                                    f"""<div
                                        id="{scroll_id}"
                                        style="
                                            height: 280px;
                                            overflow-y: auto;
                                            overflow-x: hidden;
                                            background: #f8f9fb;
                                            border: 1px solid #e2e5ea;
                                            border-radius: 6px;
                                            padding: 14px 18px;
                                            font-size: 13.5px;
                                            line-height: 1.75;
                                            white-space: pre-wrap;
                                            word-break: break-word;
                                            font-family: -apple-system, BlinkMacSystemFont,
                                                         'Segoe UI', Roboto, sans-serif;
                                            color: #2c3e50;
                                            margin-top: 10px;
                                            /* Contain scroll so mouse wheel
                                               scrolls the box, not the page */
                                            overscroll-behavior: contain;
                                            /* Modern subtle scrollbar */
                                            scrollbar-width: thin;
                                            scrollbar-color: #b0bac4 transparent;
                                        "
                                    >{safe_body}</div>
                                    <style>
                                    /* WebKit scrollbar styling */
                                    #{scroll_id}::-webkit-scrollbar {{
                                        width: 5px;
                                    }}
                                    #{scroll_id}::-webkit-scrollbar-track {{
                                        background: transparent;
                                    }}
                                    #{scroll_id}::-webkit-scrollbar-thumb {{
                                        background: #c1c9d4;
                                        border-radius: 10px;
                                    }}
                                    #{scroll_id}::-webkit-scrollbar-thumb:hover {{
                                        background: #8f9baa;
                                    }}
                                    </style>""",
                                    unsafe_allow_html=True,
                                )
                        except Exception:
                            # File unreadable — silent fallback, View button still works
                            pass

                else:
                    st.markdown(f"> {best['text'][:400]}{'...' if len(best['text']) >= 400 else ''}")

            # ── file size + download button (memory-safe) ─────────────────────
            # File size shown next to the button, matching the old behaviour.
            #
            # OOM fix: pass open(file_path, "rb") — a file object, not bytes.
            # Streamlit streams it to the browser on click and discards it.
            # Passing bytes= would keep the whole file in session state for the
            # entire session; with large PDFs across multiple users that caused
            # the RAM crash.
            if file_path and os.path.exists(file_path):
                mime_map = {
                    "pdf":   "application/pdf",
                    "docx":  "application/vnd.openxmlformats-officedocument"
                             ".wordprocessingml.document",
                    "doc":   "application/msword",
                    "xlsx":  "application/vnd.openxmlformats-officedocument"
                             ".spreadsheetml.sheet",
                    "xls":   "application/vnd.ms-excel",
                    "eml":   "message/rfc822",
                    "msg":   "application/vnd.ms-outlook",
                    "mbox":  "application/mbox",
                    "emlx":  "message/rfc822",
                }
                mime      = mime_map.get(ext, "application/octet-stream")
                file_size = os.path.getsize(file_path)
                if file_size >= 1_048_576:
                    size_str = f"{file_size / 1_048_576:.1f} MB"
                elif file_size >= 1024:
                    size_str = f"{file_size / 1024:.0f} KB"
                else:
                    size_str = f"{file_size} B"

                btn_key   = f"dl_{_render_ns}_{i}_{abs(hash(source)) % 0xFFFF:04x}"
                view_key  = f"view_{_render_ns}_{i}_{abs(hash(source)) % 0xFFFF:04x}"
                state_key = f"dl_ready_{btn_key}"

                # ── Two buttons side by side ──────────────────────────────────
                # View: opens a preview panel below (PDF only — browsers can
                #       render PDFs inline; docx/xlsx can't be previewed this way)
                # Download: two-stage to avoid loading bytes on every render
                col_view, col_dl, col_size = st.columns([2, 2, 1])

                is_email_file = ext in ("eml", "emlx", "msg", "mbox")

                with col_view:
                    if ext == "pdf" or is_email_file:
                        if st.button(
                            "View",
                            key=view_key,
                            use_container_width=True,
                        ):
                            toggle = f"show_preview_{view_key}"
                            st.session_state[toggle] = not st.session_state.get(toggle, False)
                    else:
                        st.caption(f"{ext.upper()} — download to view")

                with col_dl:
                    if state_key not in st.session_state:
                        if st.button(
                            f"Download",
                            key=btn_key,
                            use_container_width=True,
                        ):
                            try:
                                with open(file_path, "rb") as fh:
                                    st.session_state[state_key] = fh.read()
                                st.rerun()
                            except OSError as e:
                                st.error(f"Could not read file: {e}")
                    else:
                        file_bytes = st.session_state.pop(state_key)
                        st.download_button(
                            label=f"Download",
                            data=file_bytes,
                            file_name=source,
                            mime=mime,
                            key=f"{btn_key}_ready",
                            use_container_width=True,
                        )

                with col_size:
                    st.caption(f"{ext.upper()} · {size_str}")

                # ── PDF inline preview ────────────────────────────────────────
                # For small PDFs (<5MB): base64 iframe — renders full document.
                # For large PDFs: rasterise first 3 pages using pypdfium2 and
                # display as images. Avoids the blank iframe that browsers show
                # when the base64 data URI exceeds their size limit (~5MB).
                if ext == "pdf":
                    toggle = f"show_preview_{view_key}"
                    if st.session_state.get(toggle, False):
                        try:
                            file_size_mb = os.path.getsize(file_path) / 1_048_576

                            if file_size_mb <= 5:
                                # Small PDF — inline iframe
                                with open(file_path, "rb") as fh:
                                    pdf_bytes = fh.read()
                                import base64
                                b64 = base64.b64encode(pdf_bytes).decode("utf-8")
                                st.markdown(
                                    f'''<iframe
                                        src="data:application/pdf;base64,{b64}"
                                        width="100%" height="700px"
                                        style="border:1px solid #e0e0e0;
                                               border-radius:6px;">
                                    </iframe>''',
                                    unsafe_allow_html=True,
                                )
                            else:
                                # Large PDF — render first 3 pages as images
                                st.caption(
                                    f"PDF is {file_size_mb:.1f} MB — "
                                    f"showing first 3 pages as preview. "
                                    f"Use Download to get the full file."
                                )
                                try:
                                    import pypdfium2 as pdfium
                                    pdf_doc   = pdfium.PdfDocument(file_path)
                                    n_preview = min(3, len(pdf_doc))
                                    for p in range(n_preview):
                                        page   = pdf_doc[p]
                                        bitmap = page.render(scale=2.0)
                                        img    = bitmap.to_pil()
                                        st.image(
                                            img,
                                            caption=f"Page {p + 1}",
                                            width="stretch",
                                        )
                                    pdf_doc.close()
                                except Exception as pdf_err:
                                    st.warning(f"Could not render preview: {pdf_err}")
                                    st.info("Use the Download button to open the full file.")
                        except OSError as e:
                            st.error(f"Could not load preview: {e}")

                # ── Email preview panel ───────────────────────────────────────
                # Reads the file directly so the full email body is shown,
                # not just the 400-char chunk excerpt. Strips all headers,
                # encoding markers, MIME boundaries, and base64 blobs —
                # only the readable text content is displayed.
                if is_email_file:
                    toggle = f"show_preview_{view_key}"
                    if st.session_state.get(toggle, False):
                        try:
                            import email as _email
                            from email import policy as _policy

                            with open(file_path, "rb") as fh:
                                msg = _email.message_from_binary_file(
                                    fh, policy=_policy.default
                                )

                            # Extract fields
                            subject = str(msg.get("Subject", "")).strip()
                            sender  = str(msg.get("From",    "")).strip()
                            to      = str(msg.get("To",      "")).strip()
                            date    = str(msg.get("Date",    "")).strip()
                            cc      = str(msg.get("Cc",      "")).strip()

                            # Extract plain text body
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        try:
                                            body = part.get_content()
                                        except Exception:
                                            body = part.get_payload(
                                                decode=True
                                            ).decode("utf-8", errors="replace")
                                        break
                            else:
                                try:
                                    body = msg.get_content()
                                except Exception:
                                    body = msg.get_payload(
                                        decode=True
                                    ).decode("utf-8", errors="replace")

                            # Clean the body — remove blank line runs,
                            # strip lines that look like MIME/encoding noise
                            import re as _re
                            clean_lines = []
                            for line in body.splitlines():
                                stripped = line.strip()
                                # Skip MIME boundaries, base64 blobs,
                                # Content-Type lines, empty runs
                                if not stripped:
                                    if clean_lines and clean_lines[-1] != "":
                                        clean_lines.append("")
                                    continue
                                if _re.match(
                                    r"^(--|Content-|charset=|boundary=|"
                                    r"[A-Za-z0-9+/]{60,}={0,2})$",
                                    stripped
                                ):
                                    continue
                                clean_lines.append(line)
                            clean_body = "\n".join(clean_lines).strip()

                            # Render
                            st.divider()
                            st.markdown(f"### 📧 {subject}" if subject else "### 📧 Email")
                            col_a, col_b = st.columns(2)
                            with col_a:
                                if sender: st.markdown(f"**From:** {sender}")
                                if to:     st.markdown(f"**To:** {to}")
                                if cc:     st.markdown(f"**Cc:** {cc}")
                            with col_b:
                                if date:   st.markdown(f"**Date:** {date}")
                            st.divider()
                            if clean_body:
                                st.text(clean_body)
                            else:
                                st.caption("No readable text content found in this email.")

                        except Exception as e:
                            st.error(f"Could not load email preview: {e}")

            elif file_path:
                st.caption(
                    "File not available for download — re-upload to enable this."
                )

    # ── Show all files toggle ─────────────────────────────────────────────────
    if coverage and coverage["keyword_file_count"] > len(docs):
        st.divider()
        with st.expander(
            f"Show all {coverage['keyword_file_count']} files containing this keyword "
            f"(including lower-ranked results)",
            expanded=False
        ):
            st.caption(
                "These files contain the exact keyword but ranked lower "
                "than the top results above."
            )

            # Build a lookup of source → file_path from the top docs
            source_to_path = {d["source"]: d.get("file_path", "") for d in docs}

            # For files NOT in top results, fetch their path from ChromaDB
            ranked_sources_set = {d["source"] for d in docs}
            keyword_sources = coverage.get("keyword_sources", {})
            all_sources_needed = set(keyword_sources.keys()) - ranked_sources_set
            if all_sources_needed:
                try:
                    collection = get_chroma_collection()
                    for missing_src in all_sources_needed:
                        res = collection.get(
                            where={"source": {"$eq": missing_src}},
                            limit=1,
                            include=["metadatas"]
                        )
                        if res["metadatas"]:
                            m = res["metadatas"][0]
                            fp = m.get("file_path") or m.get("filepath", "")
                            if fp:
                                source_to_path[missing_src] = fp
                except Exception:
                    pass

            ranked_sources  = {d["source"] for d in docs}
            keyword_sources = coverage.get("keyword_sources", {})
            sorted_sources  = sorted(
                keyword_sources.items(), key=lambda x: x[1], reverse=True
            )

            for row_idx, (src, chunk_count) in enumerate(sorted_sources):
                status = "shown above" if src in ranked_sources else "not in top results"
                file_path = source_to_path.get(src, "")

                col_name, col_count, col_dl = st.columns([4, 2, 2])
                with col_name:
                    st.markdown(f"**{src}**")
                    st.caption(status)
                with col_count:
                    st.caption(f"{chunk_count} matching section(s)")
                with col_dl:
                    if file_path and os.path.exists(file_path):
                        ext      = src.rsplit(".", 1)[-1].lower() if "." in src else ""
                        mime_map = {
                            "pdf":  "application/pdf",
                            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "doc":  "application/msword",
                            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "xls":  "application/vnd.ms-excel",
                            "eml":  "message/rfc822",
                            "msg":  "application/vnd.ms-outlook",
                        }
                        mime     = mime_map.get(ext, "application/octet-stream")
                        dl_key    = f"expander_dl_{_render_ns}_{row_idx}_{abs(hash(src)) % 0xFFFF:04x}"
                        ready_key = f"expander_dl_ready_{dl_key}"

                        if ready_key not in st.session_state:
                            if st.button("Download", key=dl_key, use_container_width=True):
                                with st.spinner("Preparing..."):
                                    try:
                                        with open(file_path, "rb") as fh:
                                            st.session_state[ready_key] = fh.read()
                                    except OSError as e:
                                        st.error(f"Could not read: {e}")
                                st.rerun()
                        else:
                            file_bytes = st.session_state[ready_key]
                            dl_clicked = st.download_button(
                                label="Click to Download",
                                data=file_bytes,
                                file_name=src,
                                mime=mime,
                                key=f"{dl_key}_ready",
                                use_container_width=True,
                            )
                            if dl_clicked:
                                del st.session_state[ready_key]
                    else:
                        st.caption("Not available")
                st.divider()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Brand header at top ───────────────────────────────────────────────────
    st.markdown(
        "<div style='padding:12px 0 8px 0;'>"
        "<span style='font-size:18px; font-weight:700; color:#1B2A4A;'>"
        "Hawkins Data Archive</span><br>"
        "<span style='font-size:11px; color:#888;'>Internal Document Search</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Logged-in user info ───────────────────────────────────────────────────
    user     = authui.current_user()
    username = user.get("username", "") if user else ""
    role     = user.get("role", "").title() if user else ""
    dept     = user.get("dept_name") or ""
    st.markdown(
        f"<div style='font-size:13px; padding:4px 0;'>"
        f"<b>{username}</b><br>"
        f"<span style='color:#888; font-size:11px;'>{role}"
        f"{' · ' + dept if dept else ''}</span></div>",
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Navigation menu — clean sections ─────────────────────────────────────
    # DB stats
    try:
        col = get_chroma_collection()
        if authui.is_admin():
            total = col.count()
            st.caption(f"Archive: {total:,} indexed sections")
        else:
            st.caption(f"Accessible documents: {len(ALLOWED_DOC_IDS or [])}")
    except Exception:
        pass

    st.divider()

    # ── Upload Files (uploaders + admins only) ────────────────────────────────
    if authui.can_upload():
        with st.expander("Upload Files", expanded=False):
            st.caption("Drag and drop files here, or click Browse.")
            st.caption("Supported: PDF, Word, Excel, Email, ZIP, SQLite DB")

            # uploader_key increments after indexing so Streamlit renders
            # a fresh empty uploader — clearing the selected files automatically
            if "uploader_key" not in st.session_state:
                st.session_state.uploader_key = 0

            uploaded_files = st.file_uploader(
                "Drop files here",
                accept_multiple_files=True,
                type=["pdf", "docx", "doc", "xlsx", "xls",
                      "eml", "emlx", "msg", "mbox", "zip", "db"],
                label_visibility="collapsed",
                key=f"uploader_{st.session_state.uploader_key}",
            )
            if uploaded_files:
                st.caption(f"{len(uploaded_files)} file(s) selected")

            departments = authdb.list_departments()
            dept_lookup = {d["name"]: d["id"] for d in departments}

            st.caption("Visible to departments:")
            chosen_depts = st.multiselect(
                "Visible to departments",
                options=list(dept_lookup.keys()),
                label_visibility="collapsed",
            )
            make_public = st.checkbox("Visible to everyone", value=False)

            if uploaded_files:
                if not chosen_depts and not make_public:
                    st.warning("No departments selected — admins only until tagged.")

                if st.button("Start Indexing", type="primary", use_container_width=True):
                    acl = {
                        "uploaded_by": USER["username"],
                        "dept_ids":    [dept_lookup[name] for name in chosen_depts],
                        "is_public":   make_public,
                    }
                    total_new   = 0
                    total_files = len(uploaded_files)
                    progress    = st.progress(0, text="Preparing...")
                    status_box  = st.empty()
                    results     = []

                    for idx, uf in enumerate(uploaded_files):
                        pct = int((idx / total_files) * 100)
                        progress.progress(
                            idx / total_files,
                            text=f"File {idx+1} of {total_files} ({pct}%) — {uf.name}"
                        )
                        status_box.info(f"Currently indexing: {uf.name}")
                        suffix = "." + uf.name.rsplit(".", 1)[-1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uf.read())
                            tmp_path = tmp.name
                        try:
                            n = index_uploaded_file(tmp_path, uf.name, acl)
                            total_new += n
                            results.append(f"  {uf.name} — {n} sections added")
                        except Exception as e:
                            results.append(f"  {uf.name} — failed: {e}")
                        finally:
                            os.unlink(tmp_path)

                    progress.progress(1.0, text="Done")
                    status_box.empty()
                    st.success(
                        f"Done. {total_files} file(s) processed, "
                        f"{total_new} sections added."
                    )
                    with st.expander("Details", expanded=False):
                        for r in results:
                            st.text(r)
                    # Increment uploader key so the file uploader resets
                    # to empty — user doesn't have to manually remove files
                    st.session_state.uploader_key += 1
                    st.rerun()

    # ── Change Password ───────────────────────────────────────────────────────
    with st.expander("Change Password", expanded=False):
        with st.form("change_pw_sidebar"):
            old_pw  = st.text_input("Current password", type="password")
            new_pw1 = st.text_input("New password", type="password")
            new_pw2 = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Update Password", use_container_width=True)
        if submitted:
            if not authdb.authenticate(USER["username"], old_pw):
                st.error("Current password is incorrect.")
            elif len(new_pw1) < 8:
                st.error("New password must be at least 8 characters.")
            elif new_pw1 != new_pw2:
                st.error("New passwords do not match.")
            else:
                authdb.set_password(USER["id"], new_pw1)
                st.success("Password updated.")

    # ── Admin Control Panel ───────────────────────────────────────────────────
    if authui.is_admin():
        st.divider()
        # Style all page_link elements in sidebar to look like boxes
        st.markdown("""
        <style>
        [data-testid="stSidebar"] [data-testid="stPageLink"] {
            border: 1px solid #e0e0e0 !important;
            border-radius: 8px !important;
            background: #ffffff !important;
            padding: 2px 4px !important;
            margin: 0 !important;
        }
        [data-testid="stSidebar"] [data-testid="stPageLink"]:hover {
            background: #f5f5f5 !important;
        }
        [data-testid="stSidebar"] [data-testid="stPageLink"] p {
            font-size: 14px !important;
            font-weight: 500 !important;
            color: #1B2A4A !important;
        }
        </style>
        """, unsafe_allow_html=True)
        st.page_link("pages/1_Admin.py", label="›  Admin Control Panel")

    st.divider()

    # ── Search history (session-based) ──────────────────────────────────────
    with st.expander("Search History", expanded=False):
        sessions = load_session_history(USER["username"])
        if not sessions:
            st.caption("No search history yet.")
        else:
            for s_idx, session in enumerate(sessions[:30]):
                label = (
                    f"{session.get('date_label','?')}  "
                    f"({session.get('start_time','?')})  —  "
                    f"{len(session.get('queries', []))} search(es)"
                )
                with st.expander(label, expanded=False):
                    queries = session.get("queries", [])
                    for q_idx, q in enumerate(queries):
                        if st.button(
                            q[:55] + ("..." if len(q) > 55 else ""),
                            key=f"hist_s{s_idx}_q{q_idx}_{abs(hash(q))%9999}",
                            use_container_width=True,
                        ):
                            st.session_state.active_query = q
                            st.session_state.input_version += 1
                            st.rerun()
                st.divider()

    # ── Sign out ──────────────────────────────────────────────────────────────
    if st.button("Sign Out", use_container_width=True):
        # Save the current session's queries to history before logging out
        if st.session_state.get("session_queries"):
            # session_queries is newest-first; reverse to save oldest-first
            ordered = list(reversed(st.session_state.session_queries))
            save_session_to_history(USER["username"], ordered)
        authui.logout()
        st.rerun()

    st.divider()
    st.caption(f"Model: {config.OLLAMA_MODEL}")
    st.caption("Search: BGE-M3 + BM25")


# ── Main area ─────────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin-bottom:4px;'>Hawkins Data Archive</h2>"
    "<p style='color:#666; margin-top:0; margin-bottom:16px; font-size:14px;'>"
    "Search across Hawkins documents — manuals, policies, vendor files, emails and more.</p>",
    unsafe_allow_html=True,
)

# Hawkins logo watermark — fixed behind all content
st.markdown(
    f"""<div style="
        position: fixed;
        top: 62%;
        left: 55%;
        transform: translate(-50%, -50%);
        z-index: 0;
        pointer-events: none;
        opacity: 0.07;
    ">
        <img src="data:image/png;base64,{_LOGO_B64}"
             style="width: 420px; height: auto;">
    </div>""",
    unsafe_allow_html=True,
)

# ── Session state init ────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_chunks" not in st.session_state:
    st.session_state.last_chunks = []
# active_query stores the last submitted query so results persist
# across ALL reruns — including View/Download button clicks
if "active_query" not in st.session_state:
    st.session_state.active_query = ""

# Prefill from sidebar demo buttons
prefill = st.session_state.pop("prefill_question", "")
if prefill:
    st.session_state.active_query = prefill

# ── Search bar at top ─────────────────────────────────────────────────────────
# input_version increments on Clear so Streamlit treats the text_input
# as a brand new widget and renders it empty
if "input_version" not in st.session_state:
    st.session_state.input_version = 0

col_input, col_btn, col_clear = st.columns([7, 1, 1])
with col_input:
    typed = st.text_input(
        "search",
        value="",
        placeholder="Type a question or keyword...",
        label_visibility="collapsed",
        key=f"search_input_{st.session_state.input_version}",
    )
with col_btn:
    search_clicked = st.button("Search", type="primary", use_container_width=True)
with col_clear:
    clear_clicked = st.button("Clear", use_container_width=True)

if clear_clicked:
    st.session_state.active_query = ""
    st.session_state.input_version += 1
    st.session_state.messages = []
    st.session_state.last_chunks = []
    for k in list(st.session_state.keys()):
        if k.startswith("search_result_") or k.startswith("search_answer_"):
            del st.session_state[k]
    st.rerun()

# Search triggered by button click
if search_clicked and typed.strip():
    st.session_state.active_query = typed.strip()

# Search triggered by Enter key (typed is non-empty and differs from active)
elif typed.strip() and typed.strip() != st.session_state.active_query:
    st.session_state.active_query = typed.strip()

st.divider()

# ── Results area ─────────────────────────────────────────────────────────────
question = st.session_state.active_query

# Track which queries have been shown this session (most recent first)
if "session_queries" not in st.session_state:
    st.session_state.session_queries  = []
    # Generate a stable session ID once per browser session
    # Used to group all queries from this session in history
    st.session_state.session_id       = _dt.now().isoformat()
    st.session_state.session_start    = _dt.now()

if question:
    result_key = f"search_result_{question}"
    answer_key = f"search_answer_{question}"

    # Retrieve documents (cached — only runs once per unique query)
    if result_key not in st.session_state:
        with st.spinner("Searching..."):
            from retrieval.retriever import retrieve_documents
            docs, coverage = retrieve_documents(
                question,
                top_n_docs=20,
                allowed_doc_ids=ALLOWED_DOC_IDS,
            )
        st.session_state[result_key] = {"docs": docs, "coverage": coverage}

        # Save to persistent history and session list
        # Track in session list (newest first, for display below)
        if question not in st.session_state.session_queries:
            st.session_state.session_queries.insert(0, question)
            st.session_state.session_queries = st.session_state.session_queries[:20]

        # Save to disk immediately — survives crashes and browser closes
        upsert_session_query(
            USER["username"],
            st.session_state.session_id,
            question,
            st.session_state.session_start,
        )

    cached   = st.session_state[result_key]
    docs     = cached["docs"]
    coverage = cached["coverage"]

    # ── Current search results ────────────────────────────────────────────────
    render_document_results(docs, coverage=coverage)

    if docs:
        st.divider()

    # AI Answer (cached — LLM only called once per query)
    st.markdown("**Answer**")
    if answer_key not in st.session_state:
        try:
            answer = st.write_stream(stream_answer(question, docs=docs))
            chunks = st.session_state.get("last_chunks", [])
            faithfulness = _run_faithfulness_check(answer, chunks)
            _render_faithfulness_warning(faithfulness)
            st.session_state[answer_key] = {
                "answer": answer, "chunks": chunks, "faithfulness": faithfulness
            }
        except Exception as e:
            answer = f"Error generating answer: {e}"
            st.session_state[answer_key] = {"answer": answer, "chunks": [], "faithfulness": None}
    else:
        st.markdown(st.session_state[answer_key]["answer"])
        _render_faithfulness_warning(st.session_state[answer_key].get("faithfulness"))

    # ── Previous searches this session (shown below current, scroll to see) ────
    prev_queries = [q for q in st.session_state.session_queries if q != question]
    if prev_queries:
        st.divider()
        st.markdown("#### Previous Searches")
        for prev_q in prev_queries[:19]:
            prev_rk = f"search_result_{prev_q}"
            prev_ak = f"search_answer_{prev_q}"
            with st.expander(f"{prev_q}", expanded=True):
                if prev_rk in st.session_state:
                    prev_cached = st.session_state[prev_rk]
                    prev_docs   = prev_cached["docs"]
                    prev_cov    = prev_cached["coverage"]
                    render_document_results(prev_docs, coverage=prev_cov)
                    if prev_ak in st.session_state:
                        st.divider()
                        st.markdown("**Answer**")
                        st.markdown(st.session_state[prev_ak]["answer"])
                        _render_faithfulness_warning(st.session_state[prev_ak].get("faithfulness"))
                else:
                    st.caption("Results not in cache.")
                    if st.button(
                        f"Search again: {prev_q}",
                        key=f"research_{abs(hash(prev_q))%9999}"
                    ):
                        st.session_state.active_query = prev_q
                        st.rerun()