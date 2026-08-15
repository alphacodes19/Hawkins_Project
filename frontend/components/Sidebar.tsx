"use client";

import Image from "next/image";
import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Search,
  ShieldCheck,
  UploadCloud,
  LogOut,
  User,
  FolderClock,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { authApi } from "@/lib/api";
import { UploadDialog } from "./UploadDialog";
import { HistoryPanel } from "./HistoryPanel";

const COLLAPSE_KEY = "hawkins_sidebar_collapsed";

export function Sidebar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
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
        {canUpload && (
          <NavItem
            href="/uploads"
            icon={FolderClock}
            label="My Uploads"
            active={pathname === "/uploads"}
            collapsed={collapsed}
          />
        )}

        {!collapsed && (
          <div className="pt-2 mt-2 border-t border-border">
            <HistoryPanel />
          </div>
        )}
      </nav>

      <div className={`mt-auto py-4 border-t border-border space-y-1 ${collapsed ? "px-2" : "px-3"}`}>
        <UserIdentity user={user} collapsed={collapsed} />

        <Link
          href="/profile"
          title="My Profile"
          className={`flex items-center gap-2.5 text-sm rounded-md transition-colors ${
            pathname === "/profile" ? "bg-accent-soft text-accent-hover font-medium" : "text-ink-muted hover:bg-canvas"
          } ${collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2"}`}
        >
          {user.has_avatar ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={authApi.avatarUrl(user.id)}
              alt=""
              className="w-4 h-4 rounded-full object-cover shrink-0"
            />
          ) : (
            <User className="w-4 h-4 shrink-0" />
          )}
          {!collapsed && "My Profile"}
        </Link>
        <button
          onClick={handleSignOut}
          title="Sign out"
          className={`w-full flex items-center gap-2.5 text-sm rounded-md text-ink-muted hover:bg-canvas hover:text-danger transition-colors
                      ${collapsed ? "justify-center px-0 py-2.5" : "text-left px-3 py-2"}`}
        >
          <LogOut className="w-4 h-4 shrink-0" />
          {!collapsed && "Sign out"}
        </button>
      </div>

      {showUpload && <UploadDialog onClose={() => setShowUpload(false)} />}
    </aside>
  );
}

function UserIdentity({
  user,
  collapsed,
}: {
  user: { id: number; username: string; role: string; dept_name: string | null; has_avatar: boolean };
  collapsed: boolean;
}) {
  const initial = user.username.slice(0, 1).toUpperCase();
  const roleLabel = user.role.charAt(0).toUpperCase() + user.role.slice(1);
  const tooltip = `${user.username} \u00B7 ${roleLabel}${user.dept_name ? ` \u00B7 ${user.dept_name}` : ""}`;

  const avatar = user.has_avatar ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={authApi.avatarUrl(user.id)}
      alt=""
      className="w-8 h-8 rounded-full object-cover shrink-0"
    />
  ) : (
    <div className="w-8 h-8 rounded-full bg-ink text-white text-xs font-semibold flex items-center justify-center shrink-0">
      {initial}
    </div>
  );

  if (collapsed) {
    return (
      <div title={tooltip} className="flex justify-center py-1.5 mb-1">
        {avatar}
      </div>
    );
  }

  return (
    <div title={tooltip} className="flex items-center gap-2.5 px-3 py-2 mb-1 rounded-md bg-canvas/70">
      {avatar}
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink truncate">{user.username}</p>
        <div className="flex items-center gap-1.5 mt-0.5">
          <RoleBadge role={user.role} />
          {user.dept_name && <span className="text-xs text-ink-faint truncate">{user.dept_name}</span>}
        </div>
      </div>
    </div>
  );
}

function RoleBadge({ role }: { role: string }) {
  const label = role.charAt(0).toUpperCase() + role.slice(1);
  const isAdmin = role === "admin";
  return (
    <span
      className={`text-[10px] uppercase tracking-wide font-medium rounded-full px-1.5 py-0.5 border shrink-0
                  ${isAdmin ? "text-accent-hover bg-accent-soft border-accent/20" : "text-ink-faint bg-surface border-border"}`}
    >
      {label}
    </span>
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
