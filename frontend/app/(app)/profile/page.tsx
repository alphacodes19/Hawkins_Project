"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, LogOut } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { authApi, ApiError } from "@/lib/api";

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const router = useRouter();

  if (!user) return null;
  const initial = user.username.slice(0, 1).toUpperCase();

  async function handleSignOut() {
    await logout();
    router.push("/login");
  }

  return (
    <div className="w-[92%] max-w-2xl mx-auto px-4 sm:px-6 py-8">
      <h1 className="text-xl font-semibold text-ink mb-6">My Profile</h1>

      <div className="bg-surface border border-border rounded-lg p-6 mb-4">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 shrink-0 rounded-full bg-ink text-white text-xl font-semibold flex items-center justify-center">
            {initial}
          </div>
          <div>
            <p className="text-base font-medium text-ink">{user.username}</p>
            <p className="text-sm text-ink-faint">
              {user.role[0].toUpperCase() + user.role.slice(1)}
              {user.dept_name ? ` · ${user.dept_name}` : ""}
            </p>
          </div>
        </div>
      </div>

      <div className="bg-surface border border-border rounded-lg p-6 mb-4">
        <div className="flex items-center gap-2 mb-4">
          <KeyRound className="w-4 h-4 text-ink-muted" />
          <h2 className="text-sm font-semibold text-ink">Change password</h2>
        </div>
        <ChangePasswordForm />
      </div>

      <button
        onClick={handleSignOut}
        className="w-full flex items-center justify-center gap-2 border border-border hover:bg-canvas text-danger text-sm font-medium rounded-lg py-2.5 transition-colors"
      >
        <LogOut className="w-4 h-4" />
        Sign out
      </button>
    </div>
  );
}

function ChangePasswordForm() {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);
    if (newPw.length < 8) return setError("New password must be at least 8 characters.");
    if (newPw !== confirmPw) return setError("New passwords do not match.");
    try {
      await authApi.changePassword(oldPw, newPw);
      setSuccess(true);
      setOldPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update password.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 max-w-sm">
      {success && (
        <p className="text-sm text-success bg-success-soft border border-success/20 rounded-md px-3 py-2">
          Password updated.
        </p>
      )}
      <div>
        <label className="block text-xs font-medium text-ink-muted mb-1">Current password</label>
        <input
          type="password"
          value={oldPw}
          onChange={(e) => setOldPw(e.target.value)}
          className="w-full text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-ink-muted mb-1">New password</label>
        <input
          type="password"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
          className="w-full text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-ink-muted mb-1">Confirm new password</label>
        <input
          type="password"
          value={confirmPw}
          onChange={(e) => setConfirmPw(e.target.value)}
          className="w-full text-sm rounded-md border border-border px-3 py-2 focus:border-accent outline-none"
        />
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      <button
        type="submit"
        className="bg-accent hover:bg-accent-hover text-white text-sm font-medium rounded-md px-4 py-2 transition-colors"
      >
        Update password
      </button>
    </form>
  );
}
