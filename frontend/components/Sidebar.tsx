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
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useAuth } from "@/context/auth-context";
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

        {!collapsed && (
          <div className="pt-2 mt-2 border-t border-border">
            <HistoryPanel />
          </div>
        )}
      </nav>

      <div className={`mt-auto py-4 border-t border-border space-y-1 ${collapsed ? "px-2" : "px-3"}`}>
        <NavItem
          href="/profile"
          icon={User}
          label="My Profile"
          active={pathname === "/profile"}
          collapsed={collapsed}
        />
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
