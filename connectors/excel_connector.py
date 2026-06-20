import pandas as pd
import os


def extract_excel(filepath):
    """
    Reads an Excel file, sheet by sheet.
    Returns a list of dicts — one per sheet, with the sheet's data
    converted to readable text plus metadata.
    """
    documents = []
    xl = pd.ExcelFile(filepath)
    for sheet_name in xl.sheet_names:
        df = xl.parse(sheet_name)
        if df.empty:
            continue
        text = f"Sheet: {sheet_name}\n" + df.to_string(index=False)
        documents.append({
            "text": text,
            "metadata": {
                "source": os.path.basename(filepath),
                "source_type": "excel",
                "sheet": sheet_name,
                "page": 1,
                "filepath": filepath
            }
        })
    return documents


# Quick manual test
if __name__ == "__main__":
    import config
    sample_xlsx = os.path.join(config.EXCEL_DIR, os.listdir(config.EXCEL_DIR)[0])
    print(f"Testing on: {sample_xlsx}\n")
    results = extract_excel(sample_xlsx)
    print(f"Sheets extracted: {len(results)}")
    for r in results:
        print(f"\n--- Sheet: {r['metadata']['sheet']} ---")
        print(r["text"][:300])
        print("Metadata:", r["metadata"])