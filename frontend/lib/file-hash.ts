/**
 * Mirrors pipeline/doc_id.py's compute_doc_id() exactly: SHA-1 of the raw
 * bytes, truncated to the first 16 hex chars. Computed client-side via the
 * Web Crypto API so the duplicate check can run BEFORE the file is
 * uploaded — sending a 16-char hash costs nothing; sending the whole file
 * just to ask "have I already got this?" would defeat the point of
 * checking first.
 */
export async function computeDocIdHash(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-1", buffer);
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return hex.slice(0, 16);
}
