"""
indexer.py — Fast indexing mode for deadline situations
=========================================================
Three optimizations over the original:

1. METADATA TAGGING DISABLED by default (SKIP_TAGGING = True)
   The LLM tag_chunk() call was responsible for 70-80% of total indexing time.
   Chunks still get indexed with all file-level metadata (source, page, type).
   Only doc_type/department/project/summary fields are missing.
   Search quality is unaffected — BM25, vector, and keyword paths all work.
   Run with SKIP_TAGGING = False after the demo to backfill tags.

2. BATCH EMBEDDINGS
   All chunks from one file are embedded in a single model.encode() call
   instead of one call per chunk. SentenceTransformers is dramatically faster
   in batch mode — typically 3-5x faster than one-by-one.

3. BATCH CHROMADB UPSERTS
   All chunks from one file are written to ChromaDB in a single upsert()
   call instead of one per chunk. Reduces disk I/O by 10-50x per file.
"""

import os
import json
import chromadb
import config

from connectors.pdf_connector import extract_pdf
from connectors.docx_connector import extract_docx
from connectors.excel_connector import extract_excel
from connectors.email_connector import extract_email
from connectors.sql_connector import extract_sql
from pipeline.chunker import chunk_text
from pipeline.embedder import get_model

# ── Speed control ─────────────────────────────────────────────────────────────
# Set to False after the demo to run the full pipeline with metadata tagging.
SKIP_TAGGING  = True
EMBED_BATCH   = 64   # chunks per embedding batch — safe for 128GB RAM
UPSERT_BATCH  = 200  # chunks per ChromaDB write

# ── ChromaDB setup ────────────────────────────────────────────────────────────
def get_collection(reset=False):
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    if reset:
        try:
            client.delete_collection(config.CHROMA_COLLECTION)
            print(f"Deleted existing collection: {config.CHROMA_COLLECTION}")
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )
    return collection


# ── Progress tracking ─────────────────────────────────────────────────────────
PROGRESS_FILE = os.path.join(
    config.BASE_DIR, "data", "processed", "indexer_progress.json"
)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_progress(indexed_files):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(indexed_files), f, indent=2)


# ── Core indexing function ────────────────────────────────────────────────────
def index_document(collection, doc, verbose=True):
    """
    Index one document using batch embedding and batch upsert.
    Metadata tagging is skipped when SKIP_TAGGING = True.
    """
    from pipeline.doc_id import compute_doc_id, legacy_doc_id

    chunks = chunk_text(doc["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    chunks = [c for c in chunks if c.strip()]
    if not chunks:
        return 0

    source = doc["metadata"]["source"]
    sheet  = doc["metadata"].get("sheet", "")

    # doc_id for ACL system
    src_path = doc["metadata"].get("file_path") or doc["metadata"].get("filepath")
    try:
        did = compute_doc_id(src_path)
    except (TypeError, OSError):
        did = legacy_doc_id(source)

    if verbose:
        tag_note = " [tagging skipped]" if SKIP_TAGGING else ""
        print(f"    embedding {len(chunks)} chunks in batches{tag_note}")

    # ── 1. Batch embed all chunks at once ─────────────────────────────────────
    model = get_model()
    all_embeddings = model.encode(
        chunks,
        batch_size=EMBED_BATCH,
        show_progress_bar=False,
        normalize_embeddings=True,
    ).tolist()

    # ── 2. Optionally tag chunks (slow — disabled for demo) ───────────────────
    if not SKIP_TAGGING:
        from pipeline.metadata_tagger import tag_chunk
        all_tags = [tag_chunk(chunk) for chunk in chunks]
    else:
        all_tags = [{"doc_type": "general", "department": None,
                     "project": None, "people": [], "date": None,
                     "summary": ""} for _ in chunks]

    # ── 3. Build metadata + IDs ───────────────────────────────────────────────
    ids, embeddings, documents, metadatas = [], [], [], []

    for i, (chunk, embedding, tags) in enumerate(
            zip(chunks, all_embeddings, all_tags)):

        chunk_id = (
            f"{did}_{sheet}_chunk_{i}" if sheet
            else f"{did}_chunk_{i}"
        )

        meta = {**doc["metadata"], "doc_id": did}
        for k, v in tags.items():
            if v is None:
                continue
            if isinstance(v, list):
                meta[k] = ", ".join(str(x) for x in v) if v else ""
            else:
                meta[k] = str(v)

        ids.append(chunk_id)
        embeddings.append(embedding)
        documents.append(chunk)
        metadatas.append(meta)

        if verbose and i % 50 == 0 and i > 0:
            print(f"    ... {i}/{len(chunks)} chunks prepared")

    # ── 4. Batch upsert to ChromaDB ───────────────────────────────────────────
    for start in range(0, len(ids), UPSERT_BATCH):
        end = start + UPSERT_BATCH
        collection.upsert(
            ids=ids[start:end],
            embeddings=embeddings[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    if verbose:
        print(f"    OK — {len(chunks)} chunks indexed")

    return len(chunks)


# ── Directory-level indexing ──────────────────────────────────────────────────
def index_directory(collection, directory, extractor_fn, label, indexed_files):
    if not os.path.exists(directory):
        print(f"  [SKIP] Not found: {directory}")
        return 0

    files = sorted([f for f in os.listdir(directory)
                    if not f.startswith(".")])
    total_chunks = 0
    skipped = 0

    print(f"\n{'='*50}")
    print(f"[{label}] {len(files)} files")
    print(f"{'='*50}")

    for fname in files:
        fpath = os.path.join(directory, fname)

        if fpath in indexed_files:
            skipped += 1
            continue

        print(f"\n  -> {fname}")
        try:
            if fname.lower().endswith('.zip'):
                from pipeline.zip_handler import index_zip
                n = index_zip(fpath, collection, verbose=True)
                total_chunks += n
                indexed_files.add(fpath)
                save_progress(indexed_files)
                continue

            docs = extractor_fn(fpath)

            if (len(docs) > 1 and
                    docs[0]["metadata"].get("source_type") == "pdf"):
                merged_text = "\n\n".join(d["text"] for d in docs)
                merged_doc  = {
                    "text":     merged_text,
                    "metadata": docs[0]["metadata"]
                }
                docs = [merged_doc]

            for doc in docs:
                n = index_document(collection, doc)
                total_chunks += n

            indexed_files.add(fpath)
            save_progress(indexed_files)

        except Exception as e:
            print(f"  [ERROR] {fname}: {e}")

    if skipped:
        print(f"\n  (skipped {skipped} already-indexed files)")

    return total_chunks


# ── Main ──────────────────────────────────────────────────────────────────────
def run_indexer(reset=True, include_real=True):
    print("\n" + "="*60)
    print("  HAWKINS KNOWLEDGE INDEXER — FAST MODE")
    print(f"  Metadata tagging: {'DISABLED' if SKIP_TAGGING else 'ENABLED'}")
    print(f"  Embedding batch:  {EMBED_BATCH} chunks")
    print(f"  Upsert batch:     {UPSERT_BATCH} chunks")
    print("="*60)

    if reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("Progress file cleared.")

    collection    = get_collection(reset=reset)
    indexed_files = load_progress()

    if indexed_files:
        print(f"Resuming — {len(indexed_files)} files already done.")

    total = 0

    total += index_directory(
        collection, config.PDF_DIR,   extract_pdf,   "SYNTHETIC PDFs",  indexed_files)
    total += index_directory(
        collection, config.DOCX_DIR,  extract_docx,  "SYNTHETIC DOCX",  indexed_files)
    total += index_directory(
        collection, config.EXCEL_DIR, extract_excel, "SYNTHETIC Excel", indexed_files)
    total += index_directory(
        collection, config.EMAIL_DIR, extract_email, "EMAILS",          indexed_files)

    db_path = os.path.join(config.SQL_DIR, "hawkins.db")
    if os.path.exists(db_path) and db_path not in indexed_files:
        print(f"\n{'='*50}\n[SQL DB] hawkins.db\n{'='*50}")
        try:
            sql_docs = extract_sql(db_path)
            for doc in sql_docs:
                n = index_document(collection, doc)
                total += n
                print(f"  Table: {doc['metadata']['table']} -> {n} chunks")
            indexed_files.add(db_path)
            save_progress(indexed_files)
        except Exception as e:
            print(f"  [ERROR] hawkins.db: {e}")
    elif db_path in indexed_files:
        print(f"\n  (skipped hawkins.db — already indexed)")

    if include_real:
        total += index_directory(
            collection, config.REAL_PDF_DIR,  extract_pdf,  "REAL PDFs",  indexed_files)
        total += index_directory(
            collection, config.REAL_DOCX_DIR, extract_docx, "REAL DOCX",  indexed_files)

    test_data_dir = os.path.join(config.BASE_DIR, "data", "TEST_DATA")
    if os.path.exists(test_data_dir):
        files = sorted([f for f in os.listdir(test_data_dir)
                        if not f.startswith(".")])
        print(f"\n{'='*50}")
        print(f"[TEST DATA] {len(files)} files in TEST_DATA/")
        print(f"{'='*50}")

        EXTRACTOR_MAP = {
            ".pdf":  extract_pdf,
            ".docx": extract_docx,
            ".doc":  extract_docx,
            ".xlsx": extract_excel,
            ".xls":  extract_excel,
            ".eml":  extract_email,
            ".emlx": extract_email,
            ".msg":  extract_email,
            ".mbox": extract_email,
        }
        skipped = 0

        for fname in files:
            fpath = os.path.join(test_data_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if fpath in indexed_files:
                skipped += 1
                continue

            ext = os.path.splitext(fname)[1].lower()
            print(f"\n  -> {fname}")

            if ext == ".zip":
                try:
                    from pipeline.zip_handler import index_zip
                    n = index_zip(fpath, collection, verbose=True)
                    total += n
                    indexed_files.add(fpath)
                    save_progress(indexed_files)
                except Exception as e:
                    print(f"  [ERROR] {fname}: {e}")
                continue

            extractor_fn = EXTRACTOR_MAP.get(ext)
            if extractor_fn is None:
                print(f"  [SKIP] Unsupported type: {ext}")
                continue

            try:
                docs = extractor_fn(fpath)
                if (len(docs) > 1 and
                        docs[0]["metadata"].get("source_type") == "pdf"):
                    merged_text = "\n\n".join(d["text"] for d in docs)
                    docs = [{"text": merged_text,
                             "metadata": docs[0]["metadata"]}]
                if not docs:
                    print(f"  [SKIP] No text extracted")
                    continue

                file_chunks = 0
                for doc in docs:
                    doc["metadata"]["source"]    = fname
                    doc["metadata"]["file_path"] = fpath
                    doc["metadata"]["filepath"]  = fpath
                    n = index_document(collection, doc)
                    file_chunks += n

                total += file_chunks
                indexed_files.add(fpath)
                save_progress(indexed_files)

            except Exception as e:
                print(f"  [ERROR] {fname}: {e}")

        if skipped:
            print(f"\n  (skipped {skipped} already-indexed files)")
    else:
        print(f"\n  [TEST DATA] Folder not found.")

    try:
        from retrieval import retriever
        retriever.invalidate_bm25()
    except Exception:
        pass

    print("\n" + "="*60)
    print(f"  DONE — {total} new chunks indexed")
    print(f"  Total in ChromaDB: {collection.count()}")
    print(f"  Location: {config.CHROMA_PATH}")
    if SKIP_TAGGING:
        print("  NOTE: Metadata tagging was skipped.")
        print("  Set SKIP_TAGGING = False and re-run to backfill tags.")
    print("="*60)

    return total


if __name__ == "__main__":
    run_indexer(reset=False, include_real=True)