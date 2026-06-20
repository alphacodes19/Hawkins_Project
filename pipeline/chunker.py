def chunk_text(text, chunk_size=512, overlap=50):
    """
    Splits a long block of text into overlapping word-based chunks.

    Why overlap matters: if a sentence gets cut in half at a chunk boundary,
    the overlap ensures that sentence still appears whole in the NEXT chunk too,
    so the AI never loses context at the edges.
    """
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap   # move forward, but overlap by 50 words
    return chunks


# Quick manual test
if __name__ == "__main__":
    import config
    from connectors.pdf_connector import extract_pdf
    import os

    sample_pdf = os.path.join(config.PDF_DIR, "01_Company_Overview.pdf")
    pages = extract_pdf(sample_pdf)

    print(f"Pages extracted: {len(pages)}")
    total_chunks = 0
    for page in pages:
        chunks = chunk_text(page["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP)
        total_chunks += len(chunks)
        print(f"\nPage {page['metadata']['page']}: {len(page['text'].split())} words → {len(chunks)} chunk(s)")
        for i, c in enumerate(chunks):
            print(f"  Chunk {i+1}: {len(c.split())} words — starts with: \"{c[:60]}...\"")

    print(f"\nTotal chunks: {total_chunks}")