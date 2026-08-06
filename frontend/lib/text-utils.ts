/**
 * Ported from app.py's _is_garbled(). Scanned pages that Tesseract read as
 * noise come back as letter soup — this flags those chunks so the UI can
 * point people to the Download button instead of showing gibberish.
 */
export function isGarbled(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  const words = trimmed.split(/\s+/);
  if (!words.length) return true;

  const avgWordLen = words.reduce((sum, w) => sum + w.length, 0) / words.length;
  const spaceRatio = (trimmed.match(/ /g)?.length ?? 0) / trimmed.length;
  const alphaRatio = (trimmed.match(/[a-zA-Z]/g)?.length ?? 0) / trimmed.length;

  return avgWordLen > 15 || spaceRatio < 0.05 || alphaRatio < 0.4;
}

export function bestReadableChunk<T extends { text: string; score: number }>(
  chunks: T[]
): T {
  const sorted = [...chunks].sort((a, b) => b.score - a.score);
  const best = sorted[0];
  if (isGarbled(best.text) && sorted.length > 1) {
    const readable = sorted.find((c) => !isGarbled(c.text));
    if (readable) return readable;
  }
  return best;
}

export function formatBytes(n: number): string {
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}
