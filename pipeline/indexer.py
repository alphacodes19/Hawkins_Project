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
from pipeline.metadata_tagger import tag_chunk
from pipeline.embedder import embed_text


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
    chunks = chunk_text(doc["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    source = doc["metadata"]["source"]
    sheet  = doc["metadata"].get("sheet", "")

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue

        tags      = tag_chunk(chunk)
        embedding = embed_text(chunk)

        metadata = {**doc["metadata"]}
        for k, v in tags.items():
            if v is None:
                continue
            if isinstance(v, list):
                metadata[k] = ", ".join(str(x) for x in v) if v else ""
            else:
                metadata[k] = str(v)

        chunk_id = (
            f"{source}_{sheet}_chunk_{i}" if sheet
            else f"{source}_chunk_{i}"
        )

        collection.add(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[chunk],
            metadatas=[metadata]
        )

        if verbose:
            dept = tags.get("department", "")
            proj = tags.get("project", "")
            tag_str = ""
            if dept:
                tag_str += f" dept={dept}"
            if proj:
                tag_str += f" proj={proj}"
            print(f"    chunk {i} | {tags.get('doc_type','?')} "
                  f"| {len(chunk.split())} words{tag_str}")

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

        # Auto-handle ZIP files
         if fname.lower().endswith('.zip'):
            try:
                from pipeline.zip_handler import index_zip
                n = index_zip(fpath, collection, verbose=True)
                total_chunks += n
                indexed_files.add(fpath)
                save_progress(indexed_files)
            except Exception as e:
                print(f'  [ERROR] {fname} (zip): {e}')
            continue

            docs = extractor_fn(fpath)

            # Merge all pages of a PDF into one document so chunking
            # happens across the full document, not page by page.
            # Excel files are NOT merged — each sheet stays separate.
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
    print("  HAWKINS KNOWLEDGE INDEXER — FULL RUN")
    print("="*60)

    if reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("Progress file cleared.")

    collection    = get_collection(reset=reset)
    indexed_files = load_progress()

    if indexed_files:
        print(f"Resuming — {len(indexed_files)} files already done.")

    total = 0

    # Synthetic documents
    total += index_directory(
        collection, config.PDF_DIR,   extract_pdf,   "SYNTHETIC PDFs",  indexed_files)
    total += index_directory(
        collection, config.DOCX_DIR,  extract_docx,  "SYNTHETIC DOCX",  indexed_files)
    total += index_directory(
        collection, config.EXCEL_DIR, extract_excel, "SYNTHETIC Excel", indexed_files)
    total += index_directory(
        collection, config.EMAIL_DIR, extract_email, "EMAILS",          indexed_files)

    # SQL — index each table as a doc (extract_sql returns multiple docs per db)
    db_path = os.path.join(config.SQL_DIR, "hawkins.db")
    if os.path.exists(db_path) and db_path not in indexed_files:
        print(f"\n{'='*50}\n[SQL DB] hawkins.db\n{'='*50}")
        try:
            sql_docs = extract_sql(db_path)
            for doc in sql_docs:
                n = index_document(collection, doc)
                total += n
                print(f"  Table: {doc['metadata']['table']} → {n} chunks")
            indexed_files.add(db_path)
            save_progress(indexed_files)
        except Exception as e:
            print(f"  [ERROR] hawkins.db: {e}")
    elif db_path in indexed_files:
        print(f"\n  (skipped hawkins.db — already indexed)")

    # Real sourced documents
    if include_real:
        total += index_directory(
            collection, config.REAL_PDF_DIR,  extract_pdf,  "REAL PDFs",  indexed_files)
        total += index_directory(
            collection, config.REAL_DOCX_DIR, extract_docx, "REAL DOCX",  indexed_files)

    print("\n" + "="*60)
    print(f"  DONE — {total} new chunks indexed")
    print(f"  Total in ChromaDB: {collection.count()}")
    print(f"  Location: {config.CHROMA_PATH}")
    print("="*60)

    return total


if __name__ == "__main__":
    run_indexer(reset=True, include_real=True)