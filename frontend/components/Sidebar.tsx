"use client";

import Image from "next/image";
import { useState, useEffect, FormEvent } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Search,
  ShieldCheck,
  UploadCloud,
  LogOut,
  KeyRound,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { authApi, ApiError } from "@/lib/api";
import { UploadDialog } from "./UploadDialog";
import { HistoryPanel } from "./HistoryPanel";

const COLLAPSE_KEY = "hawkins_sidebar_collapsed";

export function Sidebar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [showPwForm, setShowPwForm] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  // Read synchronously from sessionStorage on first render so there's no
  // visible flash from expanded -> collapsed after mount. sessionStorage
  // (not localStorage) deliberately — "remember during the current browser
  // session" was the ask, not "remember forever."
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return sessionStorage.getItem(COLLAPSE_KEY) === "1";
  });

  useEffect(() => {
    sessionStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  if (!user) return null;

  const canUpload = user.role === "admin" || user.role === "uploader";

  async function handleSignOut() {
    await logout();
    router.push("/login");
  }

  return (
    <aside
      className={`shrink-0 border-r border-border bg-surface flex flex-col h-screen sticky top-0
                  transition-[width] duration-200 ease-out overflow-hidden ${collapsed ? "w-16" : "w-64"}`}
    >
      <div className={`py-5 border-b border-border flex items-center ${collapsed ? "justify-center px-0" : "px-5 gap-2.5"}`}>
        <div className="w-9 h-9 relative shrink-0">
          <Image src="/hawkins-logo-icon.png" alt="Hawkins Cookers Limited" fill className="object-contain" />
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink truncate">Hawkins Data Archive</p>
            <p className="text-xs text-ink-faint">Internal Document Search</p>
          </div>
        )}
      </div>

      <button
        onClick={() => setCollapsed((c) => !c)}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className={`flex items-center text-ink-faint hover:text-ink hover:bg-canvas transition-colors py-2.5 border-b border-border
                    ${collapsed ? "justify-center" : "justify-end px-4"}`}
      >
        {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
      </button>

      <nav className={`py-4 space-y-1 ${collapsed ? "px-2" : "px-3"}`}>
        <NavItem href="/" icon={Search} label="Search" active={pathname === "/"} collapsed={collapsed} />
        {user.role === "admin" && (
          <NavItem
            href="/admin"
            icon={ShieldCheck}
            label="Admin Control Panel"
            active={pathname === "/admin"}
            collapsed={collapsed}
          />
        )}
        {canUpload && (
          <button
            onClick={() => setShowUpload(true)}
            title="Upload document"
            className={`w-full flex items-center gap-2.5 text-sm rounded-md text-ink-muted hover:bg-canvas transition-colors
                        ${collapsed ? "justify-center px-0 py-2.5" : "text-left px-3 py-2"}`}
          >
            <UploadCloud className="w-4 h-4 shrink-0" />
            {!collapsed && "Upload document"}
          </button>
        )}

        {!collapsed && (
          <div className="pt-2 mt-2 border-t border-border">
            <HistoryPanel />
          </div>
        )}
      </nav>

      <div className={`mt-auto py-4 border-t border-border space-y-3 ${collapsed ? "px-2" : "px-3"}`}>
        {collapsed ? (
          <div
            className="w-8 h-8 mx-auto rounded-full bg-ink text-white text-xs font-semibold flex items-center justify-center"
            title={`${user.username} · ${user.role}`}
          >
            {user.username.slice(0, 1).toUpperCase()}
          </div>
        ) : (
          <div className="px-2">
            <p className="text-sm font-medium text-ink">{user.username}</p>
            <p className="text-xs text-ink-faint">
              {user.role[0].toUpperCase() + user.role.slice(1)}
              {user.dept_name ? ` · ${user.dept_name}` : ""}
            </p>
          </div>
        )}

        {!collapsed && (
          <>
            <button
              onClick={() => setShowPwForm((s) => !s)}
              className="w-full flex items-center gap-1.5 text-left text-xs text-ink-muted hover:text-ink px-2 transition-colors"
            >
              <KeyRound className="w-3.5 h-3.5" />
              {showPwForm ? "Cancel" : "Change password"}
            </button>
            {showPwForm && <ChangePasswordForm onDone={() => setShowPwForm(false)} />}
          </>
        )}

        <button
          onClick={handleSignOut}
          title="Sign out"
          className={`w-full flex items-center gap-2 border border-border hover:bg-canvas text-ink text-sm font-medium rounded-md transition-colors
                      ${collapsed ? "justify-center py-2" : "px-3 py-2"}`}
        >
          <LogOut className="w-4 h-4 shrink-0" />
          {!collapsed && "Sign out"}
        </button>
      </div>

      {showUpload && <UploadDialog onClose={() => setShowUpload(false)} />}
    </aside>
  );
}

function NavItem({
  href,
  icon: Icon,
  label,
  active,
  collapsed,
}: {
  href: string;
  icon: typeof Search;
  label: string;
  active: boolean;
  collapsed: boolean;
}) {
  return (
    <Link
      href={href}
      title={label}
      className={`flex items-center gap-2.5 text-sm rounded-md transition-colors ${
        active ? "bg-accent-soft text-accent-hover font-medium" : "text-ink-muted hover:bg-canvas"
      } ${collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2"}`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      {!collapsed && label}
    </Link>
  );
}

function ChangePasswordForm({ onDone }: { onDone: () => void }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPw.length < 8) return setError("New password must be at least 8 characters.");
    if (newPw !== confirmPw) return setError("New passwords do not match.");
    try {
      await authApi.changePassword(oldPw, newPw);
      setSuccess(true);
      setTimeout(onDone, 1200);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update password.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="px-2 space-y-2">
      {success ? (
        <p className="text-xs text-success">Password updated.</p>
      ) : (
        <>
          <input
            type="password"
            placeholder="Current password"
            value={oldPw}
            onChange={(e) => setOldPw(e.target.value)}
            className="w-full text-xs rounded-md border border-border px-2 py-1.5 focus:border-accent outline-none"
          />
          <input
            type="password"
            placeholder="New password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            className="w-full text-xs rounded-md border border-border px-2 py-1.5 focus:border-accent outline-none"
          />
          <input
            type="password"
            placeholder="Confirm new password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            className="w-full text-xs rounded-md border border-border px-2 py-1.5 focus:border-accent outline-none"
          />
          {error && <p className="text-xs text-danger">{error}</p>}
          <button
            type="submit"
            className="w-full bg-ink text-white text-xs font-medium rounded-md py-1.5"
          >
            Update password
          </button>
        </>
      )}
    </form>
  );
}
