import pdfplumber
import os


def extract_pdf(filepath):
    """
    Reads a PDF file page by page.
    Returns a list of dicts: one per page, each with the page's text + metadata.
    """
    documents = []
    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                documents.append({
                    "text": text.strip(),
                    "metadata": {
                        "source": os.path.basename(filepath),
                        "source_type": "pdf",
                        "page": page_num,
                        "filepath": filepath
                    }
                })
    return documents


# Quick manual test — only runs if you execute this file directly
if __name__ == "__main__":
    import config
    sample_pdf = os.path.join(config.PDF_DIR, os.listdir(config.PDF_DIR)[0])
    print(f"Testing on: {sample_pdf}\n")
    results = extract_pdf(sample_pdf)
    print(f"Pages extracted: {len(results)}")
    print(f"\n--- First page preview ---")
    print(results[0]["text"][:300])
    print(f"\n--- Metadata ---")
    print(results[0]["metadata"])