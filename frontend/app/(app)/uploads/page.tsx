"use client";

import { useEffect, useState } from "react";
import { Trash2, FolderClock } from "lucide-react";
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
              <div className="min-w-0">
                <p className="text-sm text-ink truncate">{f.source}</p>
                <p className="text-xs text-ink-faint">
                  {f.uploaded_by ?? "unknown"} · {new Date(f.created_at).toLocaleString()}
                  {f.departments.length > 0 && ` · ${f.departments.map((d) => d.name).join(", ")}`}
                  {f.is_public ? " · Public" : ""}
                  {f.hidden_by_admin ? " · Hidden by admin" : ""}
                </p>
              </div>
              {f.can_delete && (
                <button
                  onClick={() => setPendingDelete(f)}
                  title="Delete this file"
                  aria-label="Delete this file"
                  className="shrink-0 text-ink-faint hover:text-danger p-1.5 rounded-md hover:bg-danger-soft transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
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
    </div>
  );
}
