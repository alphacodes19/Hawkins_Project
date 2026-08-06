"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/lib/api";
import type { Department, FileRecord } from "@/lib/types";

export function FilesTab() {
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [filter, setFilter] = useState("");

  async function load() {
    const [f, d] = await Promise.all([adminApi.listFiles(), adminApi.listDepartments()]);
    setFiles(f);
    setDepartments(d);
  }
  useEffect(() => {
    load();
  }, []);

  const shown = filter
    ? files.filter((f) => f.source.toLowerCase().includes(filter.toLowerCase()))
    : files;

  if (!files.length) {
    return (
      <p className="text-sm text-ink-muted">
        No files registered yet. If you have an existing ChromaDB index, run{" "}
        <code className="font-mono text-xs bg-canvas px-1 py-0.5 rounded">
          python -m scripts.migrate_acl
        </code>{" "}
        to register it.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink mb-1">File visibility</h2>
        <p className="text-xs text-ink-faint mb-3">
          Every indexed file and who can reach it. Changes apply to the next query — no re-indexing.
        </p>
        <input
          placeholder="Filter by filename, e.g. Presstek"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
        <p className="text-xs text-ink-faint mt-1.5">
          {shown.length} of {files.length} files
        </p>
      </div>

      <div className="space-y-2">
        {shown.map((f) => (
          <FileRow key={f.id} file={f} departments={departments} onChanged={load} />
        ))}
      </div>
    </div>
  );
}

function FileRow({
  file,
  departments,
  onChanged,
}: {
  file: FileRecord;
  departments: Department[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<number[]>(file.departments.map((d) => d.id));
  const [isPublic, setIsPublic] = useState(!!file.is_public);
  const [hidden, setHidden] = useState(!!file.hidden_by_admin);
  const [saved, setSaved] = useState(false);

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
    await adminApi.setFileDepartments(file.doc_id, picked);
    await adminApi.setFileFlags(file.doc_id, { is_public: isPublic, hidden_by_admin: hidden });
    setSaved(true);
    onChanged();
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="border border-border rounded-lg bg-surface">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left"
      >
        <span className="text-sm text-ink truncate">
          {file.source} <span className="text-ink-faint">· {status}</span>
        </span>
        <span className="text-ink-faint text-xs shrink-0 ml-3">{open ? "Close" : "Edit"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-border pt-3 animate-fadeIn space-y-3">
          <p className="text-xs text-ink-faint font-mono">doc_id: {file.doc_id}</p>
          {file.uploaded_by && (
            <p className="text-xs text-ink-faint">Uploaded by: {file.uploaded_by}</p>
          )}

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

          <button
            onClick={handleSave}
            className="text-xs font-medium text-white bg-accent hover:bg-accent-hover rounded-md px-3 py-1.5"
          >
            {saved ? "Saved ✓" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}
