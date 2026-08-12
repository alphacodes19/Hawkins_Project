"use client";

import { useRef, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, LogOut, Camera, Trash2, Loader2 } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { authApi, ApiError } from "@/lib/api";

const MAX_AVATAR_BYTES = 5 * 1024 * 1024;
const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"];

export default function ProfilePage() {
  const { user, logout, refresh } = useAuth();
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
          <AvatarEditor user={user} initial={initial} onChanged={refresh} />
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

function AvatarEditor({
  user,
  initial,
  onChanged,
}: {
  user: { id: number; has_avatar: boolean };
  initial: string;
  onChanged: () => Promise<void>;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cache-bust so the <img> actually refetches after a replace/remove —
  // the URL is otherwise identical (keyed by user id, not by photo).
  const [version, setVersion] = useState(0);

  function pickFile() {
    setError(null);
    fileInputRef.current?.click();
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file later
    if (!file) return;

    setError(null);
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError("Please choose a JPEG, PNG, or WEBP image.");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setError("Image must be smaller than 5 MB.");
      return;
    }

    setBusy(true);
    try {
      await authApi.uploadAvatar(file);
      await onChanged();
      setVersion((v) => v + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not upload photo.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    setBusy(true);
    setError(null);
    try {
      await authApi.removeAvatar();
      await onChanged();
      setVersion((v) => v + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove photo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shrink-0">
      <div className="relative group w-14 h-14">
        {user.has_avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={version}
            src={`${authApi.avatarUrl(user.id)}?v=${version}`}
            alt="Profile photo"
            className="w-14 h-14 rounded-full object-cover border border-border"
          />
        ) : (
          <div className="w-14 h-14 rounded-full bg-ink text-white text-xl font-semibold flex items-center justify-center">
            {initial}
          </div>
        )}

        <button
          onClick={pickFile}
          disabled={busy}
          title="Change profile photo"
          aria-label="Change profile photo"
          className="absolute inset-0 rounded-full bg-ink/0 group-hover:bg-ink/50 flex items-center justify-center transition-colors disabled:cursor-wait"
        >
          {busy ? (
            <Loader2 className="w-4 h-4 text-white animate-spin" />
          ) : (
            <Camera className="w-4 h-4 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
          )}
        </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        onChange={handleFileSelected}
        className="hidden"
      />

      <div className="flex items-center gap-2 mt-1.5">
        <button
          onClick={pickFile}
          disabled={busy}
          className="text-xs font-medium text-accent hover:text-accent-hover disabled:opacity-50"
        >
          {user.has_avatar ? "Change" : "Upload"} photo
        </button>
        {user.has_avatar && (
          <button
            onClick={handleRemove}
            disabled={busy}
            className="text-xs font-medium text-ink-faint hover:text-danger disabled:opacity-50 flex items-center gap-0.5"
          >
            <Trash2 className="w-3 h-3" />
            Remove
          </button>
        )}
      </div>
      {error && <p className="text-xs text-danger mt-1 max-w-[10rem]">{error}</p>}
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
