"use client";

import { useEffect, useState, FormEvent } from "react";
import { adminApi, ApiError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Department } from "@/lib/types";

export function DepartmentsTab() {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<number, string>>({});
  const [pendingCreate, setPendingCreate] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Department | null>(null);

  async function load() {
    setDepartments(await adminApi.listDepartments());
  }
  useEffect(() => {
    load();
  }, []);

  function handleAddClick(e: FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setPendingCreate(true);
  }

  async function confirmAdd() {
    setError(null);
    setPendingCreate(false);
    try {
      await adminApi.addDepartment(newName);
      setNewName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add department.");
    }
  }

  async function handleRename(id: number) {
    setError(null);
    try {
      await adminApi.renameDepartment(id, editing[id]);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not rename department.");
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    await adminApi.deleteDepartment(pendingDelete.id);
    setPendingDelete(null);
    await load();
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-ink mb-1">Departments</h2>
        <p className="text-xs text-ink-faint mb-4">
          The seeded list is a placeholder. Replace it with Hawkins&apos; real structure — nothing
          in the code depends on these names.
        </p>

        <form onSubmit={handleAddClick} className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="New department name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
          />
          <button
            type="submit"
            className="bg-accent hover:bg-accent-hover text-white text-sm font-medium rounded-md px-4 transition-colors"
          >
            Add
          </button>
        </form>
        {error && <p className="text-sm text-danger mb-3">{error}</p>}
      </div>

      <div className="space-y-2">
        {departments.map((d) => (
          <div
            key={d.id}
            className="flex items-center gap-2 border border-border rounded-md px-3 py-2 hover:border-ink-faint/40 transition-colors"
          >
            <input
              type="text"
              value={editing[d.id] ?? d.name}
              onChange={(e) => setEditing((prev) => ({ ...prev, [d.id]: e.target.value }))}
              className="flex-1 text-sm bg-transparent outline-none"
            />
            <button
              onClick={() => handleRename(d.id)}
              className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1 transition-colors"
            >
              Rename
            </button>
            <button
              onClick={() => setPendingDelete(d)}
              className="text-xs font-medium text-danger hover:text-danger/80 border border-danger/30 rounded-md px-2.5 py-1 transition-colors"
            >
              Delete
            </button>
          </div>
        ))}
      </div>

      <p className="text-xs text-ink-faint">
        Deleting a department removes it from every file it was tagged on, and unassigns any user
        who belonged to it. Those users keep their accounts but lose department-based access until
        reassigned.
      </p>

      {pendingCreate && (
        <ConfirmDialog
          title="Create this department?"
          message={`"${newName}" will become available to tag on files and assign to users.`}
          confirmLabel="Create"
          onConfirm={confirmAdd}
          onCancel={() => setPendingCreate(false)}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete "${pendingDelete.name}"?`}
          message="This action cannot be undone. Files tagged with this department will lose that tag, and users assigned to it will be unassigned."
          confirmLabel="Delete"
          danger
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
