from docx import Document
import os


def extract_docx(filepath):
    """
    Reads a Word document.
    Returns a list with ONE dict — the whole document's text as one block,
    since .docx files don't have a reliable 'page' concept like PDFs do.
    """
    doc = Document(filepath)
    full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    if not full_text.strip():
        return []

    return [{
        "text": full_text,
        "metadata": {
            "source": os.path.basename(filepath),
            "source_type": "docx",
            "page": 1,
            "filepath": filepath
        }
    }]


# Quick manual test
# Quick manual test — using REAL sourced Hawkins documents
if __name__ == "__main__":
    import config
    sample_docx = os.path.join(config.REAL_DOCX_DIR, os.listdir(config.REAL_DOCX_DIR)[0])
    print(f"Testing on: {sample_docx}\n")
    results = extract_docx(sample_docx)
    print(f"Documents extracted: {len(results)}")
    print(f"\n--- Text preview ---")
    print(results[0]["text"][:500])
    print(f"\n--- Metadata ---")
    print(results[0]["metadata"])