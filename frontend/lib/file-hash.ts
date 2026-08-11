/**
 * file-hash.ts — client-side SHA-1 that mirrors pipeline/doc_id.py exactly
 * =========================================================================
 * Same public signature as before (`computeDocIdHash(file) -> Promise<string>`)
 * so callers don't change. What DID change (P0-3): the work now runs in a
 * Web Worker instead of the UI thread, so reading the file's bytes via
 * `file.arrayBuffer()` no longer freezes the dialog.
 *
 * Algorithm is byte-identical to the previous implementation and to
 * pipeline/doc_id.compute_doc_id(): SHA-1 of the raw bytes, truncated to
 * 16 lowercase hex chars.
 *
 * Fallback: if the browser refuses to construct the Worker (very old
 * browser, sandboxed context), we transparently degrade to the previous
 * main-thread implementation. This preserves correctness at the cost of
 * responsiveness — a broken fallback that produced a WRONG hash would be
 * far worse than a slower one that produces the right hash.
 */

// Worker & pending-request bookkeeping. A single long-lived worker
// handles every hash request in the session — cheaper than spinning one
// up per file, and the concurrency limit is enforced by the caller
// (mapWithConcurrency) not by the worker itself.
let workerSingleton: Worker | null = null;
let workerBroken = false;
let nextId = 1;
type Pending = { resolve: (h: string) => void; reject: (e: Error) => void };
const pending = new Map<number, Pending>();

function getWorker(): Worker | null {
  if (workerBroken) return null;
  if (workerSingleton) return workerSingleton;
  try {
    workerSingleton = new Worker(
      new URL("./file-hash.worker.ts", import.meta.url),
      { type: "module" }
    );
    workerSingleton.onmessage = (
      e: MessageEvent<
        | { id: number; ok: true; hash: string }
        | { id: number; ok: false; error: string }
      >
    ) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.ok) p.resolve(e.data.hash);
      else p.reject(new Error(e.data.error));
    };
    workerSingleton.onerror = (e) => {
      // Onerror only fires for uncaught worker-script errors — not
      // per-message failures (those go through the message channel
      // above). If the script itself blew up, all outstanding requests
      // are unrecoverable; reject them and mark the worker broken so
      // subsequent calls take the main-thread path.
      const err = new Error(`file-hash worker error: ${e.message || "unknown"}`);
      for (const [, p] of pending) p.reject(err);
      pending.clear();
      try {
        workerSingleton?.terminate();
      } catch {
        /* nothing to do */
      }
      workerSingleton = null;
      workerBroken = true;
      // eslint-disable-next-line no-console
      console.warn("[file-hash] worker failed; falling back to main-thread hashing.");
    };
    return workerSingleton;
  } catch (e) {
    workerBroken = true;
    // eslint-disable-next-line no-console
    console.warn(
      "[file-hash] worker unavailable; falling back to main-thread hashing.",
      e
    );
    return null;
  }
}

// Main-thread fallback — kept BYTE-IDENTICAL to the pre-P0-3
// implementation so a fallback path can never produce a different hash
// than the worker path.
async function hashOnMainThread(file: File | Blob): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-1", buffer);
  const bytes = new Uint8Array(digest);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex.slice(0, 16);
}

export async function computeDocIdHash(file: File | Blob): Promise<string> {
  const worker = getWorker();
  if (!worker) {
    return hashOnMainThread(file);
  }
  const id = nextId++;
  return new Promise<string>((resolve, reject) => {
    pending.set(id, { resolve, reject });
    try {
      worker.postMessage({ id, file });
    } catch (e) {
      pending.delete(id);
      // Serialization failure of a File through the structured clone
      // algorithm is extremely rare (File is a supported type) but
      // preserve correctness rather than silently mis-hash.
      hashOnMainThread(file).then(resolve, reject);
    }
  });
}
