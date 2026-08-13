"use client";

import { useEffect, useState } from "react";
import { Trash2, FolderClock, ExternalLink, Download } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { filesApi, ApiError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { FileRecord } from "@/lib/types";

export default function UploadsPage() {
  const { user } = useAuth();
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest">("newest");
  const [pendingDelete, setPendingDelete] = useState<FileRecord | null>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const rows = await filesApi.mine({
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        sort,
      });
      setFiles(rows);
      // Drop any selected id that's no longer in the (possibly re-filtered)
      // list, so a stale selection can't survive a filter change.
      setSelected((prev) => {
        const stillPresent = new Set(rows.map((r) => r.doc_id));
        return new Set([...prev].filter((id) => stillPresent.has(id)));
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load recent uploads.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFrom, dateTo, sort]);

  async function handleDelete(file: FileRecord) {
    setPendingDelete(null);
    try {
      await filesApi.deleteFile(file.doc_id);
      setFiles((prev) => prev.filter((f) => f.doc_id !== file.doc_id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete file.");
    }
  }

  // Only files this user is actually allowed to delete are selectable at
  // all — deleteable.can_delete already reflects "uploaded by me, or I'm
  // an admin". The backend independently re-checks ownership on every
  // single DELETE call regardless, so this is a UX convenience on top of
  // a real authorization boundary, not a substitute for one.
  const deletable = files.filter((f) => f.can_delete);
  const allDeletableSelected = deletable.length > 0 && deletable.every((f) => selected.has(f.doc_id));

  function toggleOne(docId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(docId) ? next.delete(docId) : next.add(docId);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allDeletableSelected ? new Set() : new Set(deletable.map((f) => f.doc_id)));
  }

  async function confirmBulkDelete() {
    setBulkBusy(true);
    setError(null);
    const ids = [...selected];
    const failures: string[] = [];

    // Reuses the same single-file DELETE /api/files endpoint the row-level
    // delete button uses (already independently authorized server-side per
    // request) rather than a separate bulk endpoint — one bad id in the
    // batch is reported, not silently dropped, and doesn't abort the rest.
    for (const docId of ids) {
      try {
        await filesApi.deleteFile(docId);
      } catch (err) {
        const name = files.find((f) => f.doc_id === docId)?.source ?? docId;
        failures.push(`${name}: ${err instanceof ApiError ? err.message : "failed"}`);
      }
    }

    if (failures.length > 0) {
      setError(
        `${ids.length - failures.length} of ${ids.length} deleted. Failed: ${failures.join("; ")}`
      );
    }
    setSelected(new Set());
    setConfirmingBulkDelete(false);
    setBulkBusy(false);
    await load();
  }

  if (!user) return null;

  return (
    <div className="w-[92%] max-w-4xl mx-auto px-4 sm:px-6 py-8">
      <div className="flex items-center gap-2 mb-1">
        <FolderClock className="w-5 h-5 text-ink-muted" />
        <h1 className="text-xl font-semibold text-ink">My Uploads</h1>
      </div>
      <p className="text-sm text-ink-muted mb-6">
        Files you can already see, most-recently-uploaded first. You can delete files you uploaded yourself
        {user.role === "admin" ? " — as an admin, you can delete any file here." : "."}
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-5 bg-surface border border-border rounded-lg p-3.5">
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">Sort</label>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "newest" | "oldest")}
            className="text-sm rounded-md border border-border px-2.5 py-1.5 bg-surface"
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </div>
        {(dateFrom || dateTo) && (
          <button
            onClick={() => {
              setDateFrom("");
              setDateTo("");
            }}
            className="text-xs text-ink-faint hover:text-ink underline mb-1.5"
          >
            Clear dates
          </button>
        )}
      </div>

      {error && <p className="text-sm text-danger mb-3">{error}</p>}

      {deletable.length > 0 && (
        <div className="flex items-center gap-3 text-sm mb-3">
          <label className="flex items-center gap-1.5 text-ink-muted cursor-pointer">
            <input type="checkbox" checked={allDeletableSelected} onChange={toggleAll} />
            Select all {user.role === "admin" ? "" : "of mine"}
          </label>
          {selected.size > 0 && (
            <>
              <span className="text-ink-faint">· {selected.size} selected</span>
              <button
                onClick={() => setConfirmingBulkDelete(true)}
                className="text-xs font-medium text-danger border border-danger/30 rounded-md px-2.5 py-1 flex items-center gap-1"
              >
                <Trash2 className="w-3 h-3" />
                Delete selected
              </button>
              <button
                onClick={() => setSelected(new Set())}
                className="text-xs text-ink-faint hover:text-ink underline"
              >
                Deselect all
              </button>
            </>
          )}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-12 rounded-lg bg-surface border border-border animate-pulse" />
          ))}
        </div>
      ) : files.length === 0 ? (
        <p className="text-sm text-ink-muted">No uploads found for this range.</p>
      ) : (
        <div className="space-y-2">
          {files.map((f) => (
            <div
              key={f.doc_id}
              className="flex items-center justify-between gap-3 border border-border rounded-lg bg-surface px-4 py-2.5"
            >
              <div className="flex items-center gap-3 min-w-0">
                {f.can_delete ? (
                  <input
                    type="checkbox"
                    checked={selected.has(f.doc_id)}
                    onChange={() => toggleOne(f.doc_id)}
                    className="shrink-0"
                    aria-label={`Select ${f.source}`}
                  />
                ) : (
                  // Keeps rows visually aligned even when a file isn't
                  // yours to select/delete (e.g. a shared file visible via
                  // department access) — no checkbox is ever rendered for
                  // it, so it can never be selected in the first place.
                  <span className="w-4 shrink-0" />
                )}
                <div className="min-w-0">
                  <p className="text-sm text-ink truncate">{f.source}</p>
                  <p className="text-xs text-ink-faint">
                    {f.uploaded_by ?? "unknown"} · {new Date(f.created_at).toLocaleString()}
                    {f.departments.length > 0 && ` · ${f.departments.map((d) => d.name).join(", ")}`}
                    {f.is_public ? " · Public" : ""}
                    {f.hidden_by_admin ? " · Hidden by admin" : ""}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <a
                  href={filesApi.viewUrl(f.doc_id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open in a new tab"
                  aria-label="Open in a new tab"
                  className="text-ink-faint hover:text-accent p-1.5 rounded-md hover:bg-accent-soft transition-colors"
                >
                  <ExternalLink className="w-4 h-4" />
                </a>
                <a
                  href={filesApi.downloadUrl(f.doc_id)}
                  title="Download"
                  aria-label="Download"
                  className="text-ink-faint hover:text-ink p-1.5 rounded-md hover:bg-canvas transition-colors"
                >
                  <Download className="w-4 h-4" />
                </a>
                {f.can_delete && (
                  <button
                    onClick={() => setPendingDelete(f)}
                    title="Delete this file"
                    aria-label="Delete this file"
                    className="text-ink-faint hover:text-danger p-1.5 rounded-md hover:bg-danger-soft transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete "${pendingDelete.source}"?`}
          message="This permanently removes the file from the archive, the search index, and storage. This can't be undone."
          confirmLabel="Delete"
          danger
          onConfirm={() => handleDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {confirmingBulkDelete && (
        <ConfirmDialog
          title={`Permanently delete ${selected.size} file${selected.size === 1 ? "" : "s"}?`}
          message="This removes each file from the archive, the search index, and storage. This can't be undone."
          confirmLabel="Delete"
          danger
          onConfirm={confirmBulkDelete}
          onCancel={() => !bulkBusy && setConfirmingBulkDelete(false)}
        />
      )}
    </div>
  );
}
