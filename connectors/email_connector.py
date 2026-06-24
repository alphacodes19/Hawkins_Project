import os
import email
from email import policy


def extract_email(file_path):
    """
    Parse a .eml file and return a list with one doc dict containing
    the email body + headers as text, and metadata.
    """
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)

    # Extract headers
    subject  = str(msg.get("Subject", "")).strip()
    sender   = str(msg.get("From", "")).strip()
    to       = str(msg.get("To", "")).strip()
    date_str = str(msg.get("Date", "")).strip()

    # Extract plain text body
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

    # Compose full text: headers + body (so retriever can match on subject etc.)
    full_text = f"Subject: {subject}\nFrom: {sender}\nTo: {to}\nDate: {date_str}\n\n{body}"

    fname = os.path.basename(file_path)
    return [{
        "text": full_text.strip(),
        "metadata": {
            "source":      fname,
            "source_type": "email",
            "subject":     subject,
            "sender":      sender,
            "date":        date_str,
            "file_path":   file_path,
        }
    }]


def extract_emails_from_dir(directory):
    """Extract all .eml files from a directory. Returns flat list of docs."""
    docs = []
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith(".eml"):
            fpath = os.path.join(directory, fname)
            try:
                docs.extend(extract_email(fpath))
            except Exception as e:
                print(f"  [ERROR] {fname}: {e}")
    return docs


if __name__ == "__main__":
    import config
    docs = extract_emails_from_dir(config.EMAIL_DIR)
    print(f"Extracted {len(docs)} emails\n")
    for d in docs[:3]:
        print(f"Subject: {d['metadata']['subject']}")
        print(f"From:    {d['metadata']['sender']}")
        print(f"Date:    {d['metadata']['date']}")
        print(f"Text preview: {d['text'][:150]}")
        print()