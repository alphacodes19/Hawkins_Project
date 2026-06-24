import os
import chromadb
import config

client = chromadb.PersistentClient(path=config.CHROMA_PATH)
col = client.get_collection(config.CHROMA_COLLECTION)

results = col.get(include=["metadatas"])

real_sources = set()
synthetic_sources = set()

for meta in results["metadatas"]:
    src = meta.get("source", "")
    fp = meta.get("filepath", "")

    if "real_sourced" in fp:
        real_sources.add(src)
    else:
        synthetic_sources.add(src)

print("Synthetic documents indexed:", len(synthetic_sources))
print("Real sourced documents indexed:", len(real_sources))

real_pdf_files = [
    f for f in os.listdir(config.REAL_PDF_DIR)
    if f.endswith(".pdf")
]

indexed_real = {s for s in real_sources if s.endswith(".pdf")}

print("Real PDFs in folder:", len(real_pdf_files))
print("Real PDFs indexed:", len(indexed_real))

missing = set(real_pdf_files) - indexed_real

if missing:
    print("\nMissing PDFs:")
    for f in sorted(missing):
        print("-", f)
else:
    print("\nAll PDFs indexed.")