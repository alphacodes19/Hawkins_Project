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

import config

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Hawkins Knowledge Assistant",
    page_icon="🍲",
    layout="wide"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

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


def index_uploaded_file(tmp_path, original_name):
    from pipeline.chunker import chunk_text
    from pipeline.metadata_tagger import tag_chunk
    from pipeline.embedder import embed_text

    ext = original_name.rsplit(".", 1)[-1].lower()

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
    elif ext == "eml":
        from connectors.email_connector import extract_email
        docs = extract_email(tmp_path)
    elif ext == "zip":
        from pipeline.zip_handler import index_zip
        collection = get_chroma_collection()
        return index_zip(tmp_path, collection, verbose=False)
    else:
        return 0

    collection = get_chroma_collection()
    total = 0

    for doc in docs:
        doc["metadata"]["source"] = original_name
        chunks = chunk_text(doc["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP)
        sheet  = doc["metadata"].get("sheet", "")

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            tags      = tag_chunk(chunk)
            embedding = embed_text(chunk)
            metadata  = {**doc["metadata"]}
            for k, v in tags.items():
                if v is None:
                    continue
                metadata[k] = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)

            chunk_id = (
                f"upload_{original_name}_{sheet}_chunk_{i}" if sheet
                else f"upload_{original_name}_chunk_{i}"
            )
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[metadata]
            )
            total += 1

    return total


def stream_answer(question):
    """
    Retrieve chunks, then stream the LLaMA response token by token.
    Yields text tokens. Also stores chunks in session state for display.
    """
    import ollama
    from retrieval.retriever import retrieve

    chunks = retrieve(question)
    st.session_state["last_chunks"] = chunks

    if not chunks:
        yield "I could not find this information in the available documents."
        return

    context_parts = []
    for c in chunks:
        label = c["source"]
        if c.get("page"):      label += f" | page {c['page']}"
        if c.get("source_type"): label += f" | {c['source_type']}"
        context_parts.append(f"[{label}]\n{c['text']}")

    context = "\n\n".join(context_parts)

    prompt = f"""You are a helpful enterprise knowledge assistant for Hawkins Cookers Limited.
Answer the question using ONLY the context provided below.
For every fact you state, cite the source document in brackets like [source_name].
If the answer is not in the context, say exactly: "I could not find this information in the available documents."
Never make up information.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""

    stream = ollama.chat(
        model=config.OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        stream=True
    )
    for part in stream:
        token = part["message"]["content"]
        if token:
            yield token


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🍲 Hawkins Knowledge Assistant")
    st.caption("Internal AI · IT & AI Department")
    st.divider()

    # DB stats — always fresh (no cache)
    try:
        col   = get_chroma_collection()
        count = col.count()
        st.metric("Indexed Chunks", count)
    except Exception as e:
        st.warning(f"ChromaDB error: {e}")

    st.divider()

    # ── File upload ──────────────────────────────────────────────────────────
    st.subheader("📎 Upload Documents")
    uploaded_files = st.file_uploader(
        "PDF, DOCX, XLSX, or EML",
        accept_multiple_files=True,
        type=["pdf", "docx", "xlsx", "xls", "eml", "zip"]
    )

    if uploaded_files:
        if st.button("⚡ Index Uploaded Files", type="primary", use_container_width=True):
            total_new = 0
            progress  = st.progress(0, text="Starting...")
            log       = st.empty()
            results   = []

            for idx, uf in enumerate(uploaded_files):
                progress.progress((idx) / len(uploaded_files), text=f"Processing {uf.name}...")
                suffix = "." + uf.name.rsplit(".", 1)[-1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uf.read())
                    tmp_path = tmp.name
                try:
                    n = index_uploaded_file(tmp_path, uf.name)
                    total_new += n
                    results.append(f"✅ **{uf.name}** → {n} chunks")
                except Exception as e:
                    results.append(f"❌ **{uf.name}** → {e}")
                finally:
                    os.unlink(tmp_path)

            progress.progress(1.0, text="Done!")
            log.markdown("\n\n".join(results))
            st.success(f"✅ Indexing complete — {total_new} new chunks added!")
            st.rerun()

    st.divider()

    # ── Demo questions ───────────────────────────────────────────────────────
    st.subheader("💡 Demo Questions")
    demo_questions = [
        "What is the leave policy for interns?",
        "Show me the 2025 audit approval.",
        "What is the status of Project Aurora?",
        "Summarise all documents related to Presstek.",
    ]
    for dq in demo_questions:
        if st.button(dq, use_container_width=True, key=f"demo_{dq[:20]}"):
            st.session_state["prefill_question"] = dq

    st.divider()
    st.caption(f"Model: {config.OLLAMA_MODEL} · BGE-M3 · ChromaDB")
    st.caption(f"DB: `{config.CHROMA_PATH}`")


# ── Main chat ─────────────────────────────────────────────────────────────────
st.header("🍲 Hawkins Enterprise Knowledge Assistant")
st.caption("Ask anything about Hawkins documents. Answers are grounded in your indexed files.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_chunks" not in st.session_state:
    st.session_state.last_chunks = []

# Replay chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("chunks"):
            with st.expander(f"📄 Sources — {len(set(c['source'] for c in msg['chunks']))} documents"):
                for chunk in msg["chunks"]:
                    st.markdown(f"**{chunk['source']}** · score `{chunk['score']}` · {chunk.get('source_type','')} · {chunk.get('doc_type','')}")
                    st.markdown(f"> {chunk['text'][:300]}...")
                    st.divider()

# Prefill from demo button
prefill  = st.session_state.pop("prefill_question", "")
question = st.chat_input("Ask anything about Hawkins documents...") or prefill

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            # Stream tokens directly into the UI
            answer = st.write_stream(stream_answer(question))
            chunks = st.session_state.get("last_chunks", [])

            if chunks:
                with st.expander(f"📄 Sources — {len(set(c['source'] for c in chunks))} documents, {len(chunks)} chunks"):
                    for chunk in chunks:
                        st.markdown(f"**{chunk['source']}** · score `{chunk['score']}` · {chunk.get('source_type','')} · {chunk.get('doc_type','')}")
                        st.markdown(f"> {chunk['text'][:300]}...")
                        st.divider()

            st.session_state.messages.append({
                "role":    "assistant",
                "content": answer,
                "chunks":  chunks
            })

        except Exception as e:
            err = f"⚠️ Error: {e}"
            st.error(err)
            st.session_state.messages.append({"role": "assistant", "content": err})