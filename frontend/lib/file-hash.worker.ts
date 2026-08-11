/// <reference lib="webworker" />
/**
 * file-hash.worker.ts — off-main-thread SHA-1 hashing
 * =====================================================
 * Receives {id, file} from the main thread. Reads the file into an
 * ArrayBuffer INSIDE the worker (never on the UI thread), runs
 * crypto.subtle.digest("SHA-1", ...) — the same primitive the old
 * main-thread code used — and posts back the same 16-char hex output.
 *
 * SHA-1 is BYTE-IDENTICAL to the previous implementation and to the
 * server's pipeline/doc_id.compute_doc_id() — this file changes WHERE
 * hashing happens, never WHAT it computes.
 *
 * Why not incremental/streaming SHA-1?
 * ------------------------------------
 * Web Crypto's digest() is one-shot; incremental streaming would require
 * a JS SHA-1 implementation (new dependency). For the actual file sizes
 * in this archive (PDF/DOCX/EML, typically <100 MB), the worker-heap
 * allocation from a single arrayBuffer() call is well within Chromium's
 * per-worker limits (multi-GB) and does NOT block the UI. That's the
 * property that matters. If future uploads routinely exceed ~500 MB we
 * can switch to an incremental implementation without changing the
 * outer worker/message contract.
 */

interface HashRequest {
  id: number;
  file: File | Blob;
}

interface HashSuccess {
  id: number;
  ok: true;
  hash: string;
}

interface HashError {
  id: number;
  ok: false;
  error: string;
}

async function computeHash(file: File | Blob): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-1", buffer);
  const bytes = new Uint8Array(digest);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex.slice(0, 16);
}

self.onmessage = async (event: MessageEvent<HashRequest>) => {
  const { id, file } = event.data;
  try {
    const hash = await computeHash(file);
    const reply: HashSuccess = { id, ok: true, hash };
    (self as unknown as Worker).postMessage(reply);
  } catch (e) {
    const reply: HashError = {
      id,
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
    (self as unknown as Worker).postMessage(reply);
  }
};

export {};
