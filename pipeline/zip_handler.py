import os
import zipfile
import shutil


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".eml"}


def extract_zip(zip_path, output_dir=None):
    """
    Extract a ZIP file, filter to supported formats, and sort files
    into subdirectories by type. Returns a dict of {ext: [file_paths]}.

    If output_dir is None, extracts next to the ZIP file in a folder
    named after the ZIP (without extension).
    """
    zip_name = os.path.splitext(os.path.basename(zip_path))[0]

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(zip_path), f"_unzipped_{zip_name}")

    os.makedirs(output_dir, exist_ok=True)

    # Sub-folders per type
    type_dirs = {
        ".pdf":  os.path.join(output_dir, "pdfs"),
        ".docx": os.path.join(output_dir, "docx"),
        ".doc":  os.path.join(output_dir, "docx"),
        ".xlsx": os.path.join(output_dir, "excel"),
        ".xls":  os.path.join(output_dir, "excel"),
        ".eml":  os.path.join(output_dir, "emails"),
    }
    for d in set(type_dirs.values()):
        os.makedirs(d, exist_ok=True)

    extracted = {ext: [] for ext in SUPPORTED_EXTENSIONS}
    skipped   = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            # Skip directories and hidden/system files
            if member.is_dir():
                continue
            fname = os.path.basename(member.filename)
            if fname.startswith(".") or fname.startswith("__"):
                continue

            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                skipped.append(fname)
                continue

            dest_dir  = type_dirs[ext]
            dest_path = os.path.join(dest_dir, fname)

            # Handle filename collisions
            if os.path.exists(dest_path):
                base, extension = os.path.splitext(fname)
                dest_path = os.path.join(dest_dir, f"{base}_1{extension}")

            with zf.open(member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            extracted[ext].append(dest_path)

    return {
        "output_dir": output_dir,
        "extracted":  extracted,
        "skipped":    skipped,
    }


def index_zip(zip_path, collection, verbose=True):
    """
    Full pipeline for a ZIP file:
    1. Extract + sort by type
    2. Run each file through connectors → chunker → tagger → embedder → ChromaDB
    Returns total chunks added.
    """
    import config
    from pipeline.chunker import chunk_text
    from pipeline.metadata_tagger import tag_chunk
    from pipeline.embedder import embed_text
    from connectors.pdf_connector import extract_pdf
    from connectors.docx_connector import extract_docx
    from connectors.excel_connector import extract_excel
    from connectors.email_connector import extract_email

    EXTRACTOR_MAP = {
        ".pdf":  extract_pdf,
        ".docx": extract_docx,
        ".doc":  extract_docx,
        ".xlsx": extract_excel,
        ".xls":  extract_excel,
        ".eml":  extract_email,
    }

    zip_name = os.path.basename(zip_path)
    if verbose:
        print(f"\n  [ZIP] {zip_name}")

    result = extract_zip(zip_path)
    output_dir = result["output_dir"]

    if verbose and result["skipped"]:
        print(f"    Skipped unsupported files: {result['skipped']}")

    total = 0

    for ext, file_paths in result["extracted"].items():
        if not file_paths:
            continue
        extractor = EXTRACTOR_MAP.get(ext)
        if not extractor:
            continue

        for fpath in file_paths:
            fname = os.path.basename(fpath)
            if verbose:
                print(f"    -> {fname} ({ext})")
            try:
                docs = extractor(fpath)

                # Merge PDF pages
                if ext == ".pdf" and len(docs) > 1:
                    merged_text = "\n\n".join(d["text"] for d in docs)
                    docs = [{"text": merged_text, "metadata": docs[0]["metadata"]}]

                for doc in docs:
                    # Tag source with zip origin
                    doc["metadata"]["source"]     = fname
                    doc["metadata"]["zip_source"] = zip_name

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
                            f"zip_{zip_name}_{fname}_{sheet}_chunk_{i}" if sheet
                            else f"zip_{zip_name}_{fname}_chunk_{i}"
                        )
                        collection.add(
                            ids=[chunk_id],
                            embeddings=[embedding],
                            documents=[chunk],
                            metadatas=[metadata]
                        )
                        total += 1
                        if verbose:
                            print(f"      chunk {i} | {tags.get('doc_type','?')} | {len(chunk.split())} words")

            except Exception as e:
                print(f"    [ERROR] {fname}: {e}")

    # Cleanup extracted temp folder
    shutil.rmtree(output_dir, ignore_errors=True)

    if verbose:
        print(f"    [ZIP DONE] {zip_name} → {total} chunks")
    return total


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.zip_handler <path_to_zip>")
        sys.exit(1)
    result = extract_zip(sys.argv[1])
    print(f"\nExtracted to: {result['output_dir']}")
    for ext, files in result["extracted"].items():
        if files:
            print(f"  {ext}: {len(files)} files")
    if result["skipped"]:
        print(f"  Skipped: {result['skipped']}")