"use client";

import { useEffect, useRef, useState, DragEvent } from "react";
import {
  departmentsApi,
  uploadBatchWithProgress,
  isSupportedUploadFile,
  uploadApi,
  adminApi,
} from "@/lib/api";
import type {
  UploadProgressEvent,
  UploadStage,
  BatchFile,
  BatchProgressEvent,
  DuplicateCheckResult,
} from "@/lib/api";
import { readDroppedItems, filesFromFileList } from "@/lib/file-drop";
import { computeDocIdHash } from "@/lib/file-hash";
import { mapWithConcurrency } from "@/lib/concurrency";
import type { Department } from "@/lib/types";
import { formatBytes } from "@/lib/text-utils";
import { useEscapeKey } from "@/lib/use-escape-key";
import { useAuth } from "@/context/auth-context";
import { Portal } from "./Portal";

const EXT_ICON: Record<string, string> = {
  pdf: "PDF", docx: "DOC", doc: "DOC", xlsx: "XLS", xls: "XLS",
  eml: "MAIL", msg: "MAIL", mbox: "MAIL", zip: "ZIP", db: "DB",
};

/**
 * Visually hidden but NOT display:none — kept in the layout (zero size,
 * clipped, unclickable by the user directly) rather than removed from
 * rendering entirely. Triggering .click() programmatically on a
 * display:none input is measurably less reliable across browsers (Windows
 * Chromium builds in particular) than on an element that's still actually
 * rendered, just invisible — this is the same "sr-only" technique used for
 * accessibility-hidden content, repurposed here for the same reliability
 * reason, not for accessibility.
 */
const VISUALLY_HIDDEN =
  "absolute w-px h-px p-0 -m-px overflow-hidden whitespace-nowrap border-0";
const VISUALLY_HIDDEN_STYLE: React.CSSProperties = { clip: "rect(0,0,0,0)" };

function extOf(name: string) {
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

type Resolution = "skip" | "proceed" | "replace";
interface Conflict {
  fileIndex: number;
  file: BatchFile;
  result: DuplicateCheckResult;
  resolution: Resolution;
}

export function UploadDialog({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const [departments, setDepartments] = useState<Department[]>([]);
  const [files, setFiles] = useState<BatchFile[]>([]);
  const [skippedCount, setSkippedCount] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const [selectedDepts, setSelectedDepts] = useState<number[]>([]);
  const [isPublic, setIsPublic] = useState(false);

  const [checkingDuplicates, setCheckingDuplicates] = useState(false);
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);

  const [batch, setBatch] = useState<BatchProgressEvent | null>(null);
  const [finished, setFinished] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    departmentsApi.list().then(setDepartments).catch(() => setDepartments([]));
  }, []);

  useEffect(() => {
    // Setting this imperatively via setAttribute, not as a JSX prop —
    // webkitdirectory/directory aren't part of React's typed HTMLInputElement
    // attributes, and different React/TS versions handle unknown JSX
    // attributes on native elements inconsistently. setAttribute sidesteps
    // that entirely; the browser only cares that the attribute is present,
    // not how it got there.
    folderInputRef.current?.setAttribute("webkitdirectory", "");
    folderInputRef.current?.setAttribute("directory", "");
  }, []);

  const isUploading = batch !== null && !finished;

  function addFiles(result: { files: BatchFile[]; skippedCount: number }) {
    setFiles((prev) => {
      // De-dupe by name+size — dropping the same folder twice, or a folder
      // that overlaps a previous individual pick, shouldn't double the list.
      const existingKeys = new Set(prev.map((f) => `${f.file.name}:${f.file.size}`));
      const fresh = result.files.filter((f) => !existingKeys.has(`${f.file.name}:${f.file.size}`));
      return [...prev, ...fresh];
    });
    setSkippedCount((prev) => prev + result.skippedCount);
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  function clearAll() {
    setFiles([]);
    setSkippedCount(0);
  }

  async function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(false);
    if (isUploading) return;
    const result = await readDroppedItems(e.dataTransfer);
    addFiles(result);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (!isUploading) setDragActive(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(false);
  }

  function toggleDept(id: number) {
    setSelectedDepts((prev) => (prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]));
  }

  /**
   * Runs stages 1-2 (filename + content hash) against every file BEFORE
   * any upload starts. Hashing happens client-side (Web Crypto) and the
   * check request only ever sends a 16-char hash — none of this costs a
   * real upload. Bounded to 3 concurrent files rather than firing the
   * whole batch via Promise.all — reading many files into memory
   * (file.arrayBuffer()) and hashing them all simultaneously is real
   * main-thread/memory pressure for a folder-sized batch, not "free"
   * parallelism, and was making the whole dialog feel janky right after
   * selecting a folder.
   */
  async function handleUploadClick() {
    if (!files.length) return;
    setCheckingDuplicates(true);
    try {
      const checked = await mapWithConcurrency(files, 3, async (file, fileIndex) => {
        const docId = await computeDocIdHash(file.file);
        const result = await uploadApi.checkDuplicate(file.file.name, docId);
        return { fileIndex, file, result };
      });
      const found = checked.filter((c) => c.result.verdict !== "ok");
      if (found.length === 0) {
        startUpload(files);
      } else {
        setConflicts(
          found.map((c) => ({
            ...c,
            // Sensible defaults: an exact duplicate has nothing new to add,
            // so default to skipping it; a name collision with different
            // content is most likely a genuine new version, so default to
            // proceeding (this is also exactly what happens today with no
            // check at all — the default preserves that behaviour, the
            // check just makes it visible and gives an out).
            resolution: c.result.verdict === "exact_duplicate" ? "skip" : "proceed",
          }))
        );
      }
    } finally {
      setCheckingDuplicates(false);
    }
  }

  function setResolution(fileIndex: number, resolution: Resolution) {
    setConflicts((prev) => prev && prev.map((c) => (c.fileIndex === fileIndex ? { ...c, resolution } : c)));
  }

  async function confirmConflictReview() {
    if (!conflicts) return;
    const skipIndexes = new Set(conflicts.filter((c) => c.resolution === "skip").map((c) => c.fileIndex));
    const toReplace = conflicts.filter((c) => c.resolution === "replace");

    // "Replace" hides the OLD version rather than deleting it outright —
    // reuses the existing admin file-visibility flag, so it's reversible
    // from the admin panel rather than being a real, unrecoverable delete.
    await Promise.all(
      toReplace.map((c) =>
        c.result.existing ? adminApi.setFileFlags(c.result.existing.doc_id, { hidden_by_admin: true }) : null
      )
    );

    const toUpload = files.filter((_, i) => !skipIndexes.has(i));
    setConflicts(null);
    startUpload(toUpload);
  }

  function startUpload(fileList: BatchFile[]) {
    if (!fileList.length) {
      // Every file in the batch was skipped — nothing left to do, but the
      // dialog shouldn't silently do nothing with no feedback either.
      onClose();
      return;
    }
    setFinished(false);
    const { promise, cancel } = uploadBatchWithProgress(fileList, selectedDepts, isPublic, setBatch);
    cancelRef.current = cancel;
    promise.then(() => setFinished(true));
  }

  function handleClose() {
    if (isUploading) cancelRef.current?.();
    onClose();
  }

  useEscapeKey(handleClose);

  const totalSize = files.reduce((sum, f) => sum + f.file.size, 0);
  const succeeded = batch?.results.filter((r) => r.status === "done").length ?? 0;
  const failed = batch?.results.filter((r) => r.status === "error").length ?? 0;

  return (
    <Portal>
      <div
        className="fixed inset-0 bg-ink/40 flex items-center justify-center z-50 px-4"
        onClick={handleClose}
      >
        <div
          className="bg-surface rounded-lg shadow-popover w-full max-w-lg max-h-[85vh] flex flex-col animate-fadeIn"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-6 pt-6 pb-4 shrink-0">
            <h2 className="text-base font-semibold text-ink">Upload documents</h2>
            <button onClick={handleClose} className="text-ink-faint hover:text-ink text-lg leading-none">
              ×
            </button>
          </div>

          <div className="px-6 pb-6 overflow-y-auto scrollbar-thin">
            {conflicts ? (
              <ConflictReview
                conflicts={conflicts}
                isAdmin={user?.role === "admin"}
                onChange={setResolution}
                onCancel={() => setConflicts(null)}
                onConfirm={confirmConflictReview}
              />
            ) : finished ? (
              <div className="space-y-4">
                <div
                  className={`text-sm rounded-md px-3 py-2.5 border ${
                    failed === 0
                      ? "text-success bg-success-soft border-success/20"
                      : "text-warning bg-warning-soft border-warning/20"
                  }`}
                >
                  {succeeded} of {files.length} file{files.length === 1 ? "" : "s"} indexed successfully
                  {failed > 0 ? `, ${failed} failed` : ""}.
                </div>

                {failed > 0 && (
                  <div className="space-y-1.5 max-h-40 overflow-y-auto scrollbar-thin">
                    {batch!.results
                      .filter((r) => r.status === "error")
                      .map((r, i) => (
                        <p key={i} className="text-xs text-danger">
                          <span className="font-medium">{r.file.relativePath}</span>: {r.message}
                        </p>
                      ))}
                  </div>
                )}

                <button
                  onClick={onClose}
                  className="w-full bg-ink text-white text-sm font-medium rounded-md py-2"
                >
                  Done
                </button>
              </div>
            ) : isUploading && batch ? (
              <div className="space-y-4">
                <p className="text-sm text-ink-muted">
                  File {batch.index + 1} of {batch.total}:{" "}
                  <span className="font-medium text-ink">{batch.file.relativePath}</span>
                </p>
                <UploadProgressBar progress={batch.fileProgress} />

                <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin border border-border rounded-md p-2">
                  {batch.results.map((r, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                      <StatusIcon status={r.status} active={i === batch.index} />
                      <span className={`truncate ${i === batch.index ? "text-ink font-medium" : "text-ink-muted"}`}>
                        {r.file.relativePath}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {files.length === 0 ? (
                  <div
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    className={`rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
                      dragActive ? "border-accent bg-accent-soft/40" : "border-border bg-canvas/40"
                    }`}
                  >
                    <p className="text-sm text-ink-muted mb-3">
                      Drag and drop files or a folder here
                    </p>
                    <div className="flex items-center justify-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          // Timed so we can tell, definitively, whether any
                          // delay is this app's JS or the OS file picker
                          // itself — once .click() fires, control leaves the
                          // page entirely until the OS dialog closes, so
                          // that portion can never be measured from here.
                          const t0 = performance.now();
                          fileInputRef.current?.click();
                          console.log(`[upload] click()-to-dispatch took ${(performance.now() - t0).toFixed(1)}ms`);
                        }}
                        className="text-xs font-medium text-white bg-accent hover:bg-accent-hover rounded-md px-3 py-1.5"
                      >
                        Choose files
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const t0 = performance.now();
                          folderInputRef.current?.click();
                          console.log(`[upload] click()-to-dispatch took ${(performance.now() - t0).toFixed(1)}ms`);
                        }}
                        className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-3 py-1.5"
                      >
                        Select folder
                      </button>
                    </div>
                    <p className="text-[11px] text-ink-faint mt-3">
                      PDF, Word, Excel, email, SQLite, or ZIP
                    </p>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-xs font-medium text-ink">
                        {files.length} file{files.length === 1 ? "" : "s"} selected · {formatBytes(totalSize)}
                      </p>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          className="text-xs text-accent hover:text-accent-hover"
                        >
                          Add more
                        </button>
                        <button type="button" onClick={clearAll} className="text-xs text-ink-faint hover:text-danger">
                          Clear all
                        </button>
                      </div>
                    </div>
                    <div className="max-h-52 overflow-y-auto scrollbar-thin space-y-1.5 border border-border rounded-md p-2">
                      {files.map((f, i) => {
                        const ext = extOf(f.file.name);
                        const badge = EXT_ICON[ext] ?? (ext.toUpperCase() || "FILE");
                        return (
                          <div key={`${f.file.name}-${i}`} className="flex items-center gap-2.5 px-1 py-1">
                            <span className="shrink-0 font-mono text-[10px] font-semibold text-ink-muted bg-canvas border border-border rounded px-1.5 py-0.5">
                              {badge}
                            </span>
                            <span className="flex-1 min-w-0 text-xs text-ink truncate" title={f.relativePath}>
                              {f.relativePath}
                            </span>
                            <span className="shrink-0 text-[11px] text-ink-faint">{formatBytes(f.file.size)}</span>
                            <button
                              onClick={() => removeFile(i)}
                              aria-label={`Remove ${f.file.name}`}
                              className="shrink-0 text-ink-faint hover:text-danger text-sm leading-none"
                            >
                              ✕
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {skippedCount > 0 && (
                  <p className="text-[11px] text-ink-faint">
                    {skippedCount} file{skippedCount === 1 ? "" : "s"} skipped (unsupported type).
                  </p>
                )}

                {/* Hidden inputs driving the two picker buttons above — kept
                    outside the dropzone's conditional render so "Add more"
                    can reuse the same file input once files already exist. */}
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  onChange={(e) => {
                    if (e.target.files) addFiles(filesFromFileList(e.target.files));
                    e.target.value = "";
                  }}
                  accept=".pdf,.docx,.doc,.xlsx,.xls,.eml,.msg,.mbox,.zip,.db"
                  className={VISUALLY_HIDDEN}
                  style={VISUALLY_HIDDEN_STYLE}
                  tabIndex={-1}
                  aria-hidden="true"
                />
                <input
                  ref={folderInputRef}
                  type="file"
                  onChange={(e) => {
                    if (e.target.files) addFiles(filesFromFileList(e.target.files));
                    e.target.value = "";
                  }}
                  className={VISUALLY_HIDDEN}
                  style={VISUALLY_HIDDEN_STYLE}
                  tabIndex={-1}
                  aria-hidden="true"
                />

                {files.length > 0 && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-ink mb-1.5">
                        Visible to departments
                      </label>
                      <div className="flex flex-wrap gap-1.5">
                        {departments.map((d) => (
                          <button
                            key={d.id}
                            type="button"
                            onClick={() => toggleDept(d.id)}
                            className={`text-xs rounded-full px-3 py-1 border transition-colors ${
                              selectedDepts.includes(d.id)
                                ? "bg-accent text-white border-accent"
                                : "border-border text-ink-muted hover:border-accent/40"
                            }`}
                          >
                            {d.name}
                          </button>
                        ))}
                      </div>
                    </div>

                    <label className="flex items-center gap-2 text-sm text-ink-muted">
                      <input
                        type="checkbox"
                        checked={isPublic}
                        onChange={(e) => setIsPublic(e.target.checked)}
                        className="rounded border-border text-accent focus:ring-accent"
                      />
                      Visible to everyone
                    </label>

                    <button
                      onClick={handleUploadClick}
                      disabled={checkingDuplicates}
                      className="w-full bg-accent hover:bg-accent-hover disabled:opacity-60 text-white text-sm font-medium rounded-md py-2.5 transition-colors"
                    >
                      {checkingDuplicates
                        ? "Checking for duplicates…"
                        : `Upload ${files.length} file${files.length === 1 ? "" : "s"} and index`}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </Portal>
  );
}

function ConflictReview({
  conflicts,
  isAdmin,
  onChange,
  onCancel,
  onConfirm,
}: {
  conflicts: Conflict[];
  isAdmin: boolean;
  onChange: (fileIndex: number, resolution: Resolution) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const skipCount = conflicts.filter((c) => c.resolution === "skip").length;

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium text-ink mb-1">
          {conflicts.length} file{conflicts.length === 1 ? "" : "s"} need{conflicts.length === 1 ? "s" : ""} a
          decision before uploading
        </p>
        <p className="text-xs text-ink-faint">
          Everything else in this batch has no conflict and will upload normally.
        </p>
      </div>

      <div className="space-y-2 max-h-96 overflow-y-auto scrollbar-thin">
        {conflicts.map((c) => (
          <div key={c.fileIndex} className="border border-border rounded-md p-3">
            <p className="text-sm text-ink font-medium truncate">{c.file.relativePath}</p>

            {c.result.verdict === "exact_duplicate" ? (
              <p className="text-xs text-ink-muted mt-1">
                Identical content already exists as{" "}
                <span className="font-medium">{c.result.existing?.source}</span>
                {c.result.existing?.uploaded_by ? ` (uploaded by ${c.result.existing.uploaded_by})` : ""}.
              </p>
            ) : (
              <p className="text-xs text-ink-muted mt-1">
                A different file named <span className="font-medium">{c.result.existing?.source}</span> already
                exists — this looks like a new version, not a duplicate.
              </p>
            )}

            <div className="flex flex-wrap gap-1.5 mt-2">
              <ResolutionButton
                label="Skip this file"
                active={c.resolution === "skip"}
                onClick={() => {
                  const t0 = performance.now();
                  onChange(c.fileIndex, "skip");
                  console.log(`[upload] skip resolution took ${(performance.now() - t0).toFixed(1)}ms`);
                }}
              />
              <ResolutionButton
                label={c.result.verdict === "exact_duplicate" ? "Upload anyway" : "Upload as new version"}
                active={c.resolution === "proceed"}
                onClick={() => onChange(c.fileIndex, "proceed")}
              />
              {isAdmin && c.result.verdict === "same_name_conflict" && (
                <ResolutionButton
                  label="Replace existing"
                  active={c.resolution === "replace"}
                  onClick={() => onChange(c.fileIndex, "replace")}
                  danger
                />
              )}
            </div>
          </div>
        ))}
      </div>

      {skipCount > 0 && (
        <p className="text-[11px] text-ink-faint">
          {skipCount} file{skipCount === 1 ? "" : "s"} will be skipped.
        </p>
      )}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 text-sm font-medium text-ink-muted hover:text-ink border border-border rounded-md py-2"
        >
          Back
        </button>
        <button
          onClick={onConfirm}
          className="flex-1 bg-accent hover:bg-accent-hover text-white text-sm font-medium rounded-md py-2 transition-colors"
        >
          Continue
        </button>
      </div>
    </div>
  );
}

function ResolutionButton({
  label,
  active,
  onClick,
  danger,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs rounded-full px-3 py-1 border transition-colors ${
        active
          ? danger
            ? "bg-danger text-white border-danger"
            : "bg-accent text-white border-accent"
          : "border-border text-ink-muted hover:border-accent/40"
      }`}
    >
      {label}
    </button>
  );
}

function StatusIcon({ status, active }: { status: string; active: boolean }) {
  if (status === "done") return <span className="text-success shrink-0">✓</span>;
  if (status === "error") return <span className="text-danger shrink-0">✕</span>;
  if (active) return <span className="text-accent shrink-0 animate-pulse">●</span>;
  return <span className="text-ink-faint shrink-0">○</span>;
}

const STAGE_ORDER: UploadStage[] = [
  "uploading",
  "processing",
  "extracting_text",
  "generating_embeddings",
  "indexing",
  "completed",
];

const STAGE_LABEL: Record<UploadStage, string> = {
  uploading: "Uploading",
  processing: "Processing",
  extracting_text: "Extracting text",
  generating_embeddings: "Generating embeddings",
  indexing: "Indexing",
  completed: "Completed",
  error: "Error",
};

function UploadProgressBar({ progress }: { progress: UploadProgressEvent }) {
  const currentIndex = STAGE_ORDER.indexOf(progress.stage);

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <p className="text-xs text-ink-muted">
          {STAGE_LABEL[progress.stage]}
          {progress.stage === "uploading" ? `… ${progress.percent}%` : "…"}
        </p>
        <p className="text-[11px] text-ink-faint">
          Step {Math.min(currentIndex + 1, STAGE_ORDER.length - 1)} of {STAGE_ORDER.length - 1}
        </p>
      </div>

      <div className="flex gap-1">
        {STAGE_ORDER.slice(0, -1).map((stage, i) => (
          <div key={stage} className="h-1.5 flex-1 rounded-full bg-border overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                i < currentIndex
                  ? "w-full bg-accent"
                  : i === currentIndex
                    ? stage === "uploading"
                      ? "bg-accent"
                      : "w-full bg-accent animate-pulse"
                    : "w-0"
              }`}
              style={i === currentIndex && stage === "uploading" ? { width: `${progress.percent}%` } : undefined}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
