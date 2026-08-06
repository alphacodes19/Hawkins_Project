"""
test_ocr.py — OCR diagnostic script
=====================================
Run this BEFORE trying to index any scanned PDFs.
It checks each dependency in order and tells you exactly what is and isn't working.

Usage:
    python test_ocr.py                        # checks setup only
    python test_ocr.py path/to/some.pdf       # checks setup + tests on that PDF
"""

import sys
import os

print("=" * 60)
print("  OCR DIAGNOSTIC")
print("=" * 60)
print()

# ── Step 1: pdfplumber (already used for normal PDFs) ──────────────────────
print("1. pdfplumber ...")
try:
    import pdfplumber
    print("   OK")
except ImportError:
    print("   MISSING — run:  pip install pdfplumber")
    sys.exit(1)

# ── Step 2: pypdfium2 (PDF → image, needed for scanned pages) ──────────────
print("2. pypdfium2  ...")
try:
    import pypdfium2
    print("   OK")
except ImportError:
    print("   MISSING — run:  pip install pypdfium2")
    sys.exit(1)

# ── Step 3: Pillow (image handling) ────────────────────────────────────────
print("3. Pillow     ...")
try:
    from PIL import Image
    print("   OK")
except ImportError:
    print("   MISSING — run:  pip install pillow")
    sys.exit(1)

# ── Step 4: pytesseract Python wrapper ─────────────────────────────────────
print("4. pytesseract (Python wrapper) ...")
try:
    import pytesseract
    print("   OK")
except ImportError:
    print("   MISSING — run:  pip install pytesseract")
    sys.exit(1)

# ── Step 5: Tesseract binary (the actual OCR engine) ───────────────────────
print("5. Tesseract binary ...")

# Windows: check TESSERACT_CMD env var first
tesseract_cmd = os.environ.get("TESSERACT_CMD")
if tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    print(f"   Using TESSERACT_CMD = {tesseract_cmd}")

try:
    version = pytesseract.get_tesseract_version()
    print(f"   OK  —  Tesseract {version}")
except pytesseract.TesseractNotFoundError:
    print("   NOT FOUND")
    print()
    print("   Tesseract is a system program, not a pip package.")
    print("   Install it separately:")
    print()
    print("   Windows:")
    print("     1. Download from: https://github.com/UB-Mannheim/tesseract/wiki")
    print("     2. Install it (note where — usually C:\\Program Files\\Tesseract-OCR\\)")
    print("     3. Before running the app, set this in PowerShell:")
    print("        $env:TESSERACT_CMD = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'")
    print()
    print("   Linux:")
    print("     sudo apt install tesseract-ocr")
    print()
    print("   macOS:")
    print("     brew install tesseract")
    sys.exit(1)
except Exception as e:
    print(f"   ERROR: {e}")
    sys.exit(1)

print()
print("All OCR dependencies are present.")
print()

# ── Step 6: Optional — test on a real PDF ──────────────────────────────────
if len(sys.argv) < 2:
    print("To test on an actual PDF, run:")
    print(f"   python {sys.argv[0]} path/to/your.pdf")
    sys.exit(0)

pdf_path = sys.argv[1]
if not os.path.exists(pdf_path):
    print(f"File not found: {pdf_path}")
    sys.exit(1)

print(f"Testing on: {pdf_path}")
print()

with pdfplumber.open(pdf_path) as pdf:
    total_pages  = len(pdf.pages)
    text_pages   = 0
    scanned_pages = 0
    ocr_recovered = 0

    print(f"Total pages: {total_pages}")
    print()

    for page_num, page in enumerate(pdf.pages, 1):
        text = (page.extract_text() or "").strip()

        if len(text) >= 20:
            text_pages += 1
            status = f"text ({len(text)} chars)"
        else:
            scanned_pages += 1
            # Try OCR
            try:
                pdf2 = pypdfium2.PdfDocument(pdf_path)
                pg   = pdf2[page_num - 1]
                bmp  = pg.render(scale=300 / 72)
                img  = bmp.to_pil()
                ocr_text = pytesseract.image_to_string(img).strip()
                pdf2.close()

                if len(ocr_text) >= 20:
                    ocr_recovered += 1
                    status = f"SCANNED → OCR recovered ({len(ocr_text)} chars)"
                else:
                    status = f"SCANNED → OCR found nothing (blank or unreadable page)"
            except Exception as e:
                status = f"SCANNED → OCR error: {e}"

        print(f"  Page {page_num:>3}:  {status}")

print()
print("=" * 60)
print(f"  SUMMARY")
print("=" * 60)
print(f"  Digital text pages : {text_pages}")
print(f"  Scanned pages      : {scanned_pages}")
print(f"  Recovered via OCR  : {ocr_recovered}")
if scanned_pages > 0 and ocr_recovered < scanned_pages:
    print(f"  Unreadable pages   : {scanned_pages - ocr_recovered}  (blank, corrupt, or non-English)")
print()
if scanned_pages == 0:
    print("  This PDF has a full text layer — OCR was not needed.")
elif ocr_recovered == scanned_pages:
    print("  All scanned pages recovered successfully.")
else:
    print("  Some pages could not be read. Possible causes:")
    print("  — Very poor scan quality or smudged ink")
    print("  — Non-English text (set OCR_LANG in pdf_connector.py)")
    print("  — Purely graphical pages (charts, images with no text)")