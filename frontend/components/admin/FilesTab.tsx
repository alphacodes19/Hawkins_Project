"use client";

import { useEffect, useState } from "react";
import { Trash2, EyeOff, Eye, ChevronDown, ExternalLink, Download } from "lucide-react";
import { adminApi, filesApi, ApiError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Department, FileRecord, User } from "@/lib/types";

const LIMIT_OPTIONS = [
  { label: "Top 50", value: "50" },
  { label: "Top 100", value: "100" },
  { label: "Top 200", value: "200" },
  { label: "All", value: "" },
];

export function FilesTab() {
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [uploadedBy, setUploadedBy] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest">("newest");
  const [limit, setLimit] = useState("100");

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"delete" | "hide" | "unhide" | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const rows = await adminApi.listFiles({
        q: q || undefined,
        uploaded_by: uploadedBy || undefined,
        department_id: departmentId ? Number(departmentId) : undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        sort,
        limit: limit ? Number(limit) : undefined,
      });
      setFiles(rows);
      setSelected((prev) => {
        const stillPresent = new Set(rows.map((r) => r.doc_id));
        return new Set([...prev].filter((id) => stillPresent.has(id)));
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load files.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    Promise.all([adminApi.listDepartments(), adminApi.listUsers()]).then(([d, u]) => {
      setDepartments(d);
      setUsers(u);
    });
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, uploadedBy, departmentId, dateFrom, dateTo, sort, limit]);

  const allSelected = files.length > 0 && selected.size === files.length;

  function toggleOne(docId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(docId) ? next.delete(docId) : next.add(docId);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(files.map((f) => f.doc_id)));
  }

  async function confirmBulkAction() {
    if (!bulkAction) return;
    setBulkBusy(true);
    setError(null);
    try {
      const { results } = await adminApi.bulkFileAction([...selected], bulkAction);
      const failed = results.filter((r) => !r.ok);
      if (failed.length > 0) {
        setError(
          `${results.length - failed.length} of ${results.length} succeeded. ` +
            `Failed: ${failed.map((f) => f.error).join("; ")}`
        );
      }
      setSelected(new Set());
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bulk action failed.");
    } finally {
      setBulkBusy(false);
      setBulkAction(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink mb-1">File management</h2>
        <p className="text-xs text-ink-faint mb-3">
          Every indexed file and who can reach it. Visibility changes apply to the next query — no
          re-indexing. Permanent delete removes the file from search, the database, and storage.
        </p>

        <div className="flex flex-wrap items-end gap-2.5 bg-canvas/50 border border-border rounded-lg p-3">
          <div className="flex-1 min-w-[10rem]">
            <label className="block text-xs font-medium text-ink-muted mb-1">Filename</label>
            <input
              placeholder="e.g. Presstek"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="w-full text-sm rounded-md border border-border px-2.5 py-1.5"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-muted mb-1">Uploaded by</label>
            <select
              value={uploadedBy}
              onChange={(e) => setUploadedBy(e.target.value)}
              className="text-sm rounded-md border border-border px-2.5 py-1.5 bg-surface"
            >
              <option value="">Anyone</option>
              {users.map((u) => (
                <option key={u.id} value={u.username}>
                  {u.username}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-muted mb-1">Department</label>
            <select
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
              className="text-sm rounded-md border border-border px-2.5 py-1.5 bg-surface"
            >
              <option value="">Any</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
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
          <div>
            <label className="block text-xs font-medium text-ink-muted mb-1">Show</label>
            <select
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="text-sm rounded-md border border-border px-2.5 py-1.5 bg-surface"
            >
              {LIMIT_OPTIONS.map((o) => (
                <option key={o.label} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          {(q || uploadedBy || departmentId || dateFrom || dateTo) && (
            <button
              onClick={() => {
                setQ("");
                setUploadedBy("");
                setDepartmentId("");
                setDateFrom("");
                setDateTo("");
              }}
              className="text-xs text-ink-faint hover:text-ink underline mb-1.5"
            >
              Clear filters
            </button>
          )}
        </div>

        <p className="text-xs text-ink-faint mt-2">
          {loading ? "Loading…" : `${files.length} file${files.length === 1 ? "" : "s"} shown`}
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {files.length > 0 && (
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5 text-ink-muted cursor-pointer">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} />
            Select all
          </label>
          {selected.size > 0 && (
            <>
              <span className="text-ink-faint">· {selected.size} selected</span>
              <button
                onClick={() => setBulkAction("hide")}
                className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1 flex items-center gap-1"
              >
                <EyeOff className="w-3 h-3" />
                Hide selected
              </button>
              <button
                onClick={() => setBulkAction("unhide")}
                className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1 flex items-center gap-1"
              >
                <Eye className="w-3 h-3" />
                Unhide selected
              </button>
              <button
                onClick={() => setBulkAction("delete")}
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

      {!loading && files.length === 0 && (
        <p className="text-sm text-ink-muted">
          No files match these filters. If you have an existing ChromaDB index and expect files here, run{" "}
          <code className="font-mono text-xs bg-canvas px-1 py-0.5 rounded">
            python -m scripts.migrate_acl
          </code>{" "}
          to register it.
        </p>
      )}

      <div className="space-y-2">
        {files.map((f) => (
          <FileRow
            key={f.doc_id}
            file={f}
            departments={departments}
            selected={selected.has(f.doc_id)}
            onToggleSelect={() => toggleOne(f.doc_id)}
            onChanged={load}
            onError={setError}
          />
        ))}
      </div>

      {bulkAction && (
        <ConfirmDialog
          title={
            bulkAction === "delete"
              ? `Permanently delete ${selected.size} file${selected.size === 1 ? "" : "s"}?`
              : bulkAction === "hide"
                ? `Hide ${selected.size} file${selected.size === 1 ? "" : "s"} from everyone?`
                : `Unhide ${selected.size} file${selected.size === 1 ? "" : "s"}?`
          }
          message={
            bulkAction === "delete"
              ? "This removes each file from search, the database, and storage. This can't be undone."
              : "Admins can always still see hidden files and reverse this."
          }
          confirmLabel={bulkAction === "delete" ? "Delete" : bulkAction === "hide" ? "Hide" : "Unhide"}
          danger={bulkAction === "delete"}
          onConfirm={confirmBulkAction}
          onCancel={() => !bulkBusy && setBulkAction(null)}
        />
      )}
    </div>
  );
}

function FileRow({
  file,
  departments,
  selected,
  onToggleSelect,
  onChanged,
  onError,
}: {
  file: FileRecord;
  departments: Department[];
  selected: boolean;
  onToggleSelect: () => void;
  onChanged: () => void;
  onError: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<number[]>(file.departments.map((d) => d.id));
  const [isPublic, setIsPublic] = useState(!!file.is_public);
  const [hidden, setHidden] = useState(!!file.hidden_by_admin);
  const [saved, setSaved] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const status = file.hidden_by_admin
    ? "Hidden from everyone"
    : file.is_public
      ? "Public"
      : file.departments.length
        ? file.departments.map((d) => d.name).join(", ")
        : "Admins only (untagged)";

  function toggleDept(id: number) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]));
  }

  async function handleSave() {
    try {
      await adminApi.setFileDepartments(file.doc_id, picked);
      await adminApi.setFileFlags(file.doc_id, { is_public: isPublic, hidden_by_admin: hidden });
      setSaved(true);
      onChanged();
      setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not save changes.");
    }
  }

  async function handlePermanentDelete() {
    setConfirmingDelete(false);
    try {
      const result = await adminApi.deleteFilePermanently(file.doc_id);
      if (result.warnings.length > 0) {
        onError(`Deleted with warnings: ${result.warnings.join("; ")}`);
      }
      onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not delete file.");
    }
  }

  return (
    <div className="border border-border rounded-lg bg-surface">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0"
        />
        <button onClick={() => setOpen((o) => !o)} className="flex-1 min-w-0 flex items-center justify-between text-left">
          <span className="text-sm text-ink truncate">
            {file.source} <span className="text-ink-faint">· {status}</span>
          </span>
          <ChevronDown className={`w-4 h-4 shrink-0 ml-3 text-ink-faint transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        <a
          href={filesApi.viewUrl(file.doc_id)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          title="Open in a new tab"
          aria-label="Open in a new tab"
          className="shrink-0 text-ink-faint hover:text-accent p-1.5 rounded-md hover:bg-accent-soft transition-colors"
        >
          <ExternalLink className="w-4 h-4" />
        </a>
        <a
          href={filesApi.downloadUrl(file.doc_id)}
          onClick={(e) => e.stopPropagation()}
          title="Download"
          aria-label="Download"
          className="shrink-0 text-ink-faint hover:text-ink p-1.5 rounded-md hover:bg-canvas transition-colors"
        >
          <Download className="w-4 h-4" />
        </a>
      </div>

      {open && (
        <div className="px-4 pb-4 border-t border-border pt-3 animate-fadeIn space-y-3">
          <p className="text-xs text-ink-faint font-mono">doc_id: {file.doc_id}</p>
          {file.uploaded_by && (
            <p className="text-xs text-ink-faint">Uploaded by: {file.uploaded_by}</p>
          )}
          <p className="text-xs text-ink-faint">
            Uploaded: {new Date(file.created_at).toLocaleString()}
          </p>

          <div>
            <p className="text-xs font-medium text-ink mb-1.5">Visible to departments</p>
            <div className="flex flex-wrap gap-1.5">
              {departments.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => toggleDept(d.id)}
                  className={`text-xs rounded-full px-3 py-1 border transition-colors ${
                    picked.includes(d.id)
                      ? "bg-accent text-white border-accent"
                      : "border-border text-ink-muted hover:border-accent/40"
                  }`}
                >
                  {d.name}
                </button>
              ))}
            </div>
          </div>

          <div className="flex gap-4">
            <label className="flex items-center gap-1.5 text-sm text-ink-muted">
              <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} />
              Visible to everyone
            </label>
            <label className="flex items-center gap-1.5 text-sm text-danger">
              <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} />
              Hide from everyone
            </label>
          </div>

          <div className="flex items-center gap-2">
            <a
              href={filesApi.viewUrl(file.doc_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-3 py-1.5 flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" />
              Open in new tab
            </a>
            <a
              href={filesApi.downloadUrl(file.doc_id)}
              className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-3 py-1.5 flex items-center gap-1"
            >
              <Download className="w-3 h-3" />
              Download
            </a>
            <button
              onClick={handleSave}
              className="text-xs font-medium text-white bg-accent hover:bg-accent-hover rounded-md px-3 py-1.5"
            >
              {saved ? "Saved ✓" : "Save"}
            </button>
            <button
              onClick={() => setConfirmingDelete(true)}
              className="text-xs font-medium text-danger border border-danger/30 rounded-md px-3 py-1.5 flex items-center gap-1"
            >
              <Trash2 className="w-3 h-3" />
              Delete permanently
            </button>
          </div>
        </div>
      )}

      {confirmingDelete && (
        <ConfirmDialog
          title={`Permanently delete "${file.source}"?`}
          message="This removes the file from search, the database, and storage. This can't be undone — it's different from hiding, which keeps the file and lets you reverse it later."
          confirmLabel="Delete"
          danger
          onConfirm={handlePermanentDelete}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </div>
  );
}
