import type { BatchFile } from "./api";
import { isSupportedUploadFile } from "./api";

/**
 * The Directory Entries API (webkitGetAsEntry, FileSystemDirectoryReader,
 * etc.) is broadly supported (Chrome/Edge/Firefox/Safari) but still
 * inconsistently typed across TS DOM lib versions, and TS's own built-in
 * FileSystemEntry type doesn't line up with what browsers actually hand
 * back from webkitGetAsEntry() in practice. `any` at this specific boundary
 * is a deliberate, narrow choice — everything that CONSUMES these values
 * below is normal, fully-typed code; only the raw browser API surface is
 * untyped, which is honest given how legacy/non-standard it actually is.
 */
type RawEntry = any;

function readAllDirectoryEntries(reader: RawEntry): Promise<RawEntry[]> {
  return new Promise((resolve, reject) => {
    const all: RawEntry[] = [];
    function readBatch() {
      // readEntries() is NOT guaranteed to return every entry in one call
      // for large directories — the API requires calling it repeatedly
      // until it returns an empty array. Easy to miss and silently drop
      // files in a big folder if you only call it once.
      reader.readEntries((batch: RawEntry[]) => {
        if (batch.length === 0) {
          resolve(all);
          return;
        }
        all.push(...batch);
        readBatch();
      }, reject);
    }
    readBatch();
  });
}

async function walkEntry(entry: RawEntry, out: BatchFile[]): Promise<void> {
  if (!entry) return;
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => entry.file(resolve, reject));
    out.push({ file, relativePath: String(entry.fullPath || file.name).replace(/^\//, "") });
  } else if (entry.isDirectory) {
    const children = await readAllDirectoryEntries(entry.createReader());
    for (const child of children) {
      await walkEntry(child, out);
    }
  }
}

export interface DroppedFilesResult {
  files: BatchFile[];
  skippedCount: number; // unsupported extensions, filtered out
}

/**
 * Reads a drop event's DataTransferItemList, recursively expanding any
 * dropped folders into their full file contents. Falls back to flat files
 * (no folder support) on browsers/contexts where webkitGetAsEntry isn't
 * available — still works for plain multi-file drag-and-drop either way.
 */
export async function readDroppedItems(dataTransfer: DataTransfer): Promise<DroppedFilesResult> {
  const collected: BatchFile[] = [];
  const items = Array.from(dataTransfer.items) as RawEntry[];

  const entries = items
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
    .filter((e: RawEntry) => !!e);

  if (entries.length > 0) {
    for (const entry of entries) {
      await walkEntry(entry, collected);
    }
  } else {
    // No directory-entry support in this browser context — just use the
    // flat file list dataTransfer always provides as a fallback.
    for (const file of Array.from(dataTransfer.files)) {
      collected.push({ file, relativePath: file.name });
    }
  }

  return filterSupported(collected);
}

/** Same filtering/shaping, for a plain <input type="file"> selection
 *  (including one with `webkitdirectory`, which populates webkitRelativePath). */
export function filesFromFileList(fileList: FileList): DroppedFilesResult {
  const collected: BatchFile[] = Array.from(fileList).map((file) => ({
    file,
    relativePath: (file as RawEntry).webkitRelativePath || file.name,
  }));
  return filterSupported(collected);
}

function filterSupported(files: BatchFile[]): DroppedFilesResult {
  const supported = files.filter((f) => isSupportedUploadFile(f.file.name));
  return { files: supported, skippedCount: files.length - supported.length };
}
