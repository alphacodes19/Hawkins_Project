"""
email_connector.py — Multi-format email extraction
====================================================
Supported formats:
  .eml   — Standard RFC 822 email (most email clients export this)
  .emlx  — Apple Mail format (same structure as .eml, different wrapper bytes)
  .msg   — Microsoft Outlook binary format (requires extract-msg)
  .mbox  — Unix mailbox: one file containing many emails (indexes each one)

All formats return the same dict shape:
  { "text": <subject + headers + body>, "metadata": { ... } }

Graceful degradation:
  .msg   needs  pip install extract-msg  — if missing, falls back to a warning doc
  .mbox  uses Python's standard mailbox module — no extra install needed
  .emlx  the Apple wrapper bytes are skipped; the RFC 822 payload is extracted

The upload form in app.py and the batch indexer in indexer.py both call
extract_email(filepath). The dispatcher at the bottom of this file routes
to the right handler based on file extension, so callers don't need to know
which format they're dealing with.
"""

import os
import email
from email import policy


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_body(msg):
    """Pull plain-text body from a parsed email.Message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                except Exception:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
    return body.strip()


def _msg_to_doc(msg, source_path, source_type="email"):
    """Convert a parsed email.Message into the standard doc dict."""
    subject  = str(msg.get("Subject", "")).strip()
    sender   = str(msg.get("From",    "")).strip()
    to       = str(msg.get("To",      "")).strip()
    date_str = str(msg.get("Date",    "")).strip()
    body     = _extract_body(msg)

    full_text = (
        f"Subject: {subject}\nFrom: {sender}\nTo: {to}\nDate: {date_str}\n\n{body}"
    )

    return {
        "text": full_text.strip(),
        "metadata": {
            "source":      os.path.basename(source_path),
            "source_type": source_type,
            "subject":     subject,
            "sender":      sender,
            "date":        date_str,
            "file_path":   source_path,
            "filepath":    source_path,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_eml(file_path):
    """Standard .eml — RFC 822 format used by Thunderbird, Gmail export, etc."""
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    return [_msg_to_doc(msg, file_path, source_type="email")]


def _extract_emlx(file_path):
    """
    Apple Mail .emlx format.
    An emlx file is an eml file with a short integer prefix on the first line
    (the byte length of the RFC 822 payload) and optional Apple XML plist
    metadata appended at the end. We skip the first line and parse the rest
    as a normal RFC 822 message.
    """
    with open(file_path, "rb") as f:
        lines = f.readlines()

    # First line is the payload byte count — skip it
    if lines and lines[0].strip().isdigit():
        lines = lines[1:]

    raw = b"".join(lines)
    msg = email.message_from_bytes(raw, policy=policy.default)
    return [_msg_to_doc(msg, file_path, source_type="email_emlx")]


def _extract_msg(file_path):
    """
    Microsoft Outlook .msg binary format.
    Requires:  pip install extract-msg
    Falls back to a placeholder doc if the package is not installed, so the
    system doesn't crash — it just won't have the content of that file.
    """
    try:
        import extract_msg as em
        with em.Message(file_path) as msg:
            subject  = (msg.subject  or "").strip()
            sender   = (msg.sender   or "").strip()
            to       = (msg.to       or "").strip()
            date_str = str(msg.date  or "").strip()
            body     = (msg.body     or "").strip()
    except ImportError:
        # extract-msg not installed — return a stub so indexing continues
        fname = os.path.basename(file_path)
        return [{
            "text": f"[Outlook .msg file: {fname}] Install extract-msg to index this file.",
            "metadata": {
                "source":      fname,
                "source_type": "email_msg",
                "subject":     "",
                "sender":      "",
                "date":        "",
                "file_path":   file_path,
                "filepath":    file_path,
            },
        }]
    except Exception as e:
        fname = os.path.basename(file_path)
        return [{
            "text": f"[Could not read {fname}: {e}]",
            "metadata": {
                "source":      fname,
                "source_type": "email_msg",
                "subject":     "",
                "sender":      "",
                "date":        "",
                "file_path":   file_path,
                "filepath":    file_path,
            },
        }]

    full_text = (
        f"Subject: {subject}\nFrom: {sender}\nTo: {to}\nDate: {date_str}\n\n{body}"
    )
    return [{
        "text": full_text.strip(),
        "metadata": {
            "source":      os.path.basename(file_path),
            "source_type": "email_msg",
            "subject":     subject,
            "sender":      sender,
            "date":        date_str,
            "file_path":   file_path,
            "filepath":    file_path,
        },
    }]


def _extract_mbox(file_path):
    """
    Unix mbox format — one file containing multiple emails.
    Python's standard mailbox module handles this; no extra install needed.
    Each email in the mbox becomes a separate doc.
    """
    import mailbox

    docs = []
    try:
        mbox = mailbox.mbox(file_path, factory=None, create=False)
        for i, msg in enumerate(mbox):
            try:
                doc = _msg_to_doc(msg, file_path, source_type="email_mbox")
                # Distinguish emails within the same mbox file
                doc["metadata"]["source"] = f"{os.path.basename(file_path)}#msg{i}"
                docs.append(doc)
            except Exception as e:
                print(f"  [mbox] skipping message {i}: {e}")
        mbox.close()
    except Exception as e:
        print(f"  [mbox] could not open {file_path}: {e}")

    if not docs:
        # Return a stub rather than an empty list so the caller can report it
        docs.append({
            "text": f"[mbox file {os.path.basename(file_path)} contained no readable emails]",
            "metadata": {
                "source":      os.path.basename(file_path),
                "source_type": "email_mbox",
                "file_path":   file_path,
                "filepath":    file_path,
            },
        })
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API — single entry point for all callers
# ─────────────────────────────────────────────────────────────────────────────

# All extensions this connector handles. Used by indexer.py and app.py to
# decide whether to route a file here.
SUPPORTED_EXTENSIONS = {".eml", ".emlx", ".msg", ".mbox"}


def extract_email(file_path):
    """
    Dispatch to the right handler based on file extension.
    Always returns a list of doc dicts (may be more than one for .mbox).
    Never raises — on any unrecognised extension it returns a stub doc.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".eml":
        return _extract_eml(file_path)
    elif ext == ".emlx":
        return _extract_emlx(file_path)
    elif ext == ".msg":
        return _extract_msg(file_path)
    elif ext == ".mbox":
        return _extract_mbox(file_path)
    else:
        # Unknown format — return a placeholder rather than crashing
        fname = os.path.basename(file_path)
        return [{
            "text": f"[Unsupported email format: {fname}]",
            "metadata": {
                "source":      fname,
                "source_type": "email_unknown",
                "file_path":   file_path,
                "filepath":    file_path,
            },
        }]


def extract_emails_from_dir(directory):
    """Index every supported email file in a directory. Returns flat doc list."""
    docs = []
    for fname in sorted(os.listdir(directory)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            fpath = os.path.join(directory, fname)
            try:
                docs.extend(extract_email(fpath))
            except Exception as e:
                print(f"  [ERROR] {fname}: {e}")
    return docs


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        results = extract_email(sys.argv[1])
        for r in results:
            print(f"Subject : {r['metadata'].get('subject', '—')}")
            print(f"From    : {r['metadata'].get('sender', '—')}")
            print(f"Type    : {r['metadata']['source_type']}")
            print(f"Preview : {r['text'][:200]}")
            print()
    else:
        import config
        docs = extract_emails_from_dir(config.EMAIL_DIR)
        print(f"Extracted {len(docs)} emails")
        for d in docs[:3]:
            print(f"  {d['metadata']['source']} — {d['metadata'].get('subject','')}")