"use client";

import { useEffect, useState, FormEvent } from "react";
import { adminApi, ApiError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Department, User } from "@/lib/types";

const ROLES = ["admin", "uploader", "viewer"] as const;
const NO_DEPT = -1;

export function UsersTab() {
  const [users, setUsers] = useState<User[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [u, d] = await Promise.all([adminApi.listUsers(), adminApi.listDepartments()]);
    setUsers(u);
    setDepartments(d);
  }
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Users</h2>
        <button
          onClick={() => setShowCreate((s) => !s)}
          className="text-xs font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-3 py-1.5"
        >
          {showCreate ? "Cancel" : "Create a user"}
        </button>
      </div>

      {showCreate && (
        <CreateUserForm
          departments={departments}
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
        />
      )}

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="space-y-2">
        {users.map((u) => (
          <UserRow
            key={u.id}
            user={u}
            departments={departments}
            onChanged={load}
            onError={(m) => setError(m)}
          />
        ))}
      </div>
    </div>
  );
}

function CreateUserForm({
  departments,
  onCreated,
}: {
  departments: Department[];
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("viewer");
  const [deptId, setDeptId] = useState<number>(NO_DEPT);
  const [error, setError] = useState<string | null>(null);
  const [pendingCreate, setPendingCreate] = useState(false);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) return setError("Password must be at least 8 characters.");
    setPendingCreate(true);
  }

  async function confirmCreate() {
    setPendingCreate(false);
    try {
      await adminApi.createUser(username, password, role, deptId === NO_DEPT ? null : deptId);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create user.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border border-border rounded-lg p-4 space-y-3 bg-canvas/40">
      <div className="grid grid-cols-2 gap-3">
        <input
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
        <input
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}
          className="text-sm rounded-md border border-border px-3 py-2 bg-surface"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select
          value={deptId}
          onChange={(e) => setDeptId(Number(e.target.value))}
          className="text-sm rounded-md border border-border px-3 py-2 bg-surface"
        >
          <option value={NO_DEPT}>— none —</option>
          {departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      <button
        type="submit"
        className="bg-accent hover:bg-accent-hover text-white text-sm font-medium rounded-md px-4 py-2"
      >
        Create user
      </button>
      {pendingCreate && (
        <ConfirmDialog
          title="Create this user?"
          message={`"${username}" will be created with the ${role} role${deptId !== NO_DEPT ? " and assigned to the selected department" : ""}.`}
          confirmLabel="Create"
          onConfirm={confirmCreate}
          onCancel={() => setPendingCreate(false)}
        />
      )}
    </form>
  );
}

function UserRow({
  user,
  departments,
  onChanged,
  onError,
}: {
  user: User;
  departments: Department[];
  onChanged: () => void;
  onError: (m: string) => void;
}) {
  const [role, setRole] = useState<User["role"]>(user.role);
  const [deptId, setDeptId] = useState<number>(user.dept_id ?? NO_DEPT);
  const [active, setActive] = useState(user.is_active);
  const [newPassword, setNewPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);

  async function handleSave() {
    try {
      await adminApi.updateUser(user.id, {
        role,
        dept_id: deptId === NO_DEPT ? null : deptId,
        is_active: active,
        ...(newPassword ? { new_password: newPassword } : {}),
      });
      setNewPassword("");
      onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not update user.");
    }
  }

  async function handleDelete() {
    setPendingDelete(false);
    try {
      await adminApi.deleteUser(user.id);
      onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not delete user.");
    }
  }

  return (
    <div className="border border-border rounded-lg bg-surface">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left"
      >
        <span className="text-sm text-ink">
          {user.username} <span className="text-ink-faint">· {user.role}</span>
          {user.dept_name && <span className="text-ink-faint"> · {user.dept_name}</span>}
          {!user.is_active && <span className="text-danger"> · DISABLED</span>}
        </span>
        <span className="text-ink-faint text-xs">{open ? "Close" : "Edit"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-border pt-3 animate-fadeIn space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as User["role"])}
              className="text-sm rounded-md border border-border px-2 py-1.5 bg-surface"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              value={deptId}
              onChange={(e) => setDeptId(Number(e.target.value))}
              className="text-sm rounded-md border border-border px-2 py-1.5 bg-surface"
            >
              <option value={NO_DEPT}>— none —</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-sm text-ink-muted">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
              Active
            </label>
          </div>
          <input
            type="password"
            placeholder="Reset password (leave blank to keep current)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="w-full text-sm rounded-md border border-border px-3 py-1.5"
          />
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              className="text-xs font-medium text-white bg-ink rounded-md px-3 py-1.5"
            >
              Save
            </button>
            <button
              onClick={() => setPendingDelete(true)}
              className="text-xs font-medium text-danger border border-danger/30 rounded-md px-3 py-1.5"
            >
              Delete user
            </button>
          </div>
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete "${user.username}"?`}
          message="This action cannot be undone. The account will be permanently removed."
          confirmLabel="Delete"
          danger
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(false)}
        />
      )}
    </div>
  );
}
