"""
pdf_connector.py — PDF text extraction with OCR fallback
=========================================================
extract_pdf() first tries pdfplumber's text layer. When a page comes back empty
— which is what a scanned or image-only page looks like — that single page is
rasterised and passed through Tesseract.

The fallback is per PAGE, not per FILE. Real archives are full of hybrid PDFs:
a typed cover letter followed by a scanned signature page, or a report with a
photographed annexure. A per-file check ("does this PDF have any text at all?")
would see text on page 1 and silently drop the scanned pages.

Chunks produced by OCR carry `ocr: "true"` in their metadata. OCR text is
noisier than a real text layer, so being able to identify it later matters when
debugging a bad retrieval result.

DEPENDENCIES — these are not pure pip:
  pip install pytesseract pypdfium2 pillow
  ...plus the Tesseract *engine* itself, which is a system binary:
    Windows : https://github.com/UB-Mannheim/tesseract/wiki  (then set
              TESSERACT_CMD below, or the TESSERACT_CMD env var)
    Linux   : sudo apt install tesseract-ocr
    macOS   : brew install tesseract

If either the Python packages or the engine are missing, OCR degrades quietly:
extract_pdf() still returns whatever text layer exists and logs one warning.
It never raises. A missing OCR engine must not take the whole indexer down.
"""

import os

# ── OCR tuning ───────────────────────────────────────────────────────────────
OCR_DPI        = 300    # 300 is the standard floor for reliable OCR; 150 loses accuracy
OCR_LANG       = "eng+hin"  # English + Hindi (install hin.traineddata for Hindi support)
MIN_TEXT_CHARS = 20     # a page with fewer real characters than this is treated as
                        # scanned — catches pages holding only a header or page number

# Set explicitly if Tesseract is not on PATH (typical on Windows).
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")

_ocr_available = None   # tri-state: None = not yet probed, True/False = probed


def _probe_ocr() -> bool:
    """Check once whether OCR is usable. Cached; never raises."""
    global _ocr_available
    if _ocr_available is not None:
        return _ocr_available
    try:
        import pytesseract
        import pypdfium2  # noqa: F401  (import is the availability check)

        if TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        pytesseract.get_tesseract_version()   # raises if the binary is absent
        _ocr_available = True
    except Exception as e:
        print(f"  [OCR] unavailable, scanned pages will be skipped — {e}")
        _ocr_available = False
    return _ocr_available


def _ocr_page(filepath: str, page_index: int) -> str:
    """
    Rasterise one page and OCR it. page_index is 0-based.

    pypdfium2 is used rather than pdf2image because pdf2image shells out to
    Poppler, which is another system binary to install on Windows. pypdfium2
    ships a self-contained wheel.
    """
    try:
        import pypdfium2 as pdfium
        import pytesseract

        pdf    = pdfium.PdfDocument(filepath)
        page   = pdf[page_index]
        # scale is relative to 72 dpi, PDF's native unit
        bitmap = page.render(scale=OCR_DPI / 72)
        image  = bitmap.to_pil()
        text   = pytesseract.image_to_string(image, lang=OCR_LANG)
        pdf.close()
        return text.strip()
    except Exception as e:
        print(f"  [OCR] page {page_index + 1} failed: {e}")
        return ""


def extract_pdf(filepath, enable_ocr=True):
    """
    Read a PDF page by page.
    Returns a list of dicts — one per page that yielded usable text — each with
    the page's text plus metadata.

    Pages whose text layer is empty are OCR'd when enable_ocr is True and the
    OCR toolchain is present. Pages that yield nothing either way are dropped,
    exactly as before.
    """
    import pdfplumber

    documents = []
    ocr_pages = 0

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text     = (page.extract_text() or "").strip()
            used_ocr = False

            if len(text) < MIN_TEXT_CHARS and enable_ocr and _probe_ocr():
                ocr_text = _ocr_page(filepath, page_num - 1)
                # Only accept the OCR result if it beat the text layer.
                # A blank page OCRs to noise; don't let noise replace nothing.
                if len(ocr_text) > len(text):
                    text      = ocr_text
                    used_ocr  = True
                    ocr_pages += 1

            if not text:
                continue

            documents.append({
                "text": text,
                "metadata": {
                    "source":      os.path.basename(filepath),
                    "source_type": "pdf",
                    "page":        page_num,
                    "filepath":    filepath,
                    # Chroma metadata values must be scalars — store as a string,
                    # not a bool, to match how every other tag is written.
                    "ocr":         "true" if used_ocr else "false",
                },
            })

    if ocr_pages:
        print(f"  [OCR] {os.path.basename(filepath)}: {ocr_pages} page(s) recovered via OCR")

    return documents


# Quick manual test
if __name__ == "__main__":
    import sys
    import config

    if len(sys.argv) > 1:
        sample_pdf = sys.argv[1]
    else:
        sample_pdf = os.path.join(config.PDF_DIR, os.listdir(config.PDF_DIR)[0])

    print(f"Testing on: {sample_pdf}")
    print(f"OCR available: {_probe_ocr()}\n")

    results = extract_pdf(sample_pdf)
    print(f"Pages extracted: {len(results)}")
    if results:
        ocr_count = sum(1 for r in results if r["metadata"]["ocr"] == "true")
        print(f"Pages via OCR:   {ocr_count}")
        print("\n--- First page preview ---")
        print(results[0]["text"][:300])
        print("\n--- Metadata ---")
        print(results[0]["metadata"])
