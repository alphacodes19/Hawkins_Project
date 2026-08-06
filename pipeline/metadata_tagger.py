import ollama
import json
import config
import time


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

_DEFAULT_TAGS = {
    "project":    None,
    "department": None,
    "people":     [],
    "date":       None,
    "doc_type":   "general",
    "summary":    "",
}


def _safe_defaults(text=""):
    tags = dict(_DEFAULT_TAGS)
    tags["summary"] = text[:100].strip() if text else ""
    return tags


def tag_chunk(text, retries=2):
    """
    Send one chunk to the local LLM and extract structured metadata.
    Never raises. Always returns a dict with the required keys.
    Retries on connection errors. Falls back to safe defaults on any failure.
    """
    for attempt in range(retries + 1):
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{
                    "role": "user",
                    "content": TAGGING_PROMPT.format(text=text[:4000])
                }]
            )

            if response is None:
                if attempt < retries:
                    time.sleep(3); continue
                return _safe_defaults(text)

            message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
            if message is None:
                if attempt < retries:
                    time.sleep(3); continue
                return _safe_defaults(text)

            raw = (message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "") or "").strip()

            if not raw:
                if attempt < retries:
                    time.sleep(3); continue
                return _safe_defaults(text)

            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            try:
                tags = json.loads(raw)
            except json.JSONDecodeError:
                import re
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    try:
                        tags = json.loads(match.group())
                    except json.JSONDecodeError:
                        return _safe_defaults(text)
                else:
                    return _safe_defaults(text)

            if not isinstance(tags, dict):
                return _safe_defaults(text)

            for key, default in _DEFAULT_TAGS.items():
                if key not in tags:
                    tags[key] = default

            return tags

        except KeyboardInterrupt:
            raise

        except Exception as e:
            err = str(e)
            is_conn = any(x in err for x in [
                "WinError 10054", "connection", "connect", "10054",
                "forcibly closed", "Failed to connect", "RemoteDisconnected"
            ])
            if is_conn and attempt < retries:
                print(f"    [tagger] connection error, retrying in 5s: {err[:80]}")
                time.sleep(5)
                continue
            print(f"    [tagger] error: {err[:120]}")
            return _safe_defaults(text)

    return _safe_defaults(text)


if __name__ == "__main__":
    from connectors.pdf_connector import extract_pdf
    import os
    sample_pdf = os.path.join(config.PDF_DIR, "02_Project_P001_Project_Aurora_Report.pdf")
    pages = extract_pdf(sample_pdf)
    print(f"Testing on: {os.path.basename(sample_pdf)}\n")
    tags = tag_chunk(pages[0]["text"])
    print(json.dumps(tags, indent=2))