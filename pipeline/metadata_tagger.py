import ollama
import json
import config


TAGGING_PROMPT = """You are a metadata extractor for a company knowledge base.
Read the text below and return ONLY a JSON object with these exact fields:

- project: project name if mentioned (e.g. "Project Aurora"), else null
- department: department name if mentioned, else null
- people: list of person names mentioned, else []
- date: most relevant date mentioned, in YYYY-MM-DD format if possible, else null
- doc_type: one of [report, policy, email, specification, invoice, audit, recipe, catalog, manual, general]
- summary: one sentence summary of what this chunk is about

Return ONLY the raw JSON object. No explanation. No markdown formatting. No code fences.

TEXT:
{text}
"""


def tag_chunk(text):
    """
    Sends one chunk of text to the local LLaMA model and asks it to
    extract structured metadata. Falls back to safe defaults if the
    model's response isn't valid JSON.
    """
    response = ollama.chat(
        model=config.OLLAMA_MODEL,
        messages=[{"role": "user", "content": TAGGING_PROMPT.format(text=text[:2000])}]
    )

    raw = response["message"]["content"].strip()

    # Strip markdown code fences if the model adds them anyway
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        tags = json.loads(raw)
    except json.JSONDecodeError:
        tags = {
            "project": None, "department": None, "people": [],
            "date": None, "doc_type": "general",
            "summary": text[:100].strip()
        }

    return tags


# Quick manual test
if __name__ == "__main__":
    from connectors.pdf_connector import extract_pdf
    import os

    sample_pdf = os.path.join(config.PDF_DIR, "02_Project_P001_Project_Aurora_Report.pdf")
    pages = extract_pdf(sample_pdf)

    print(f"Testing metadata tagging on: {os.path.basename(sample_pdf)}\n")
    print(f"--- Original text (first 200 chars) ---")
    print(pages[0]["text"][:200])

    print(f"\n--- Extracted tags ---")
    tags = tag_chunk(pages[0]["text"])
    print(json.dumps(tags, indent=2))