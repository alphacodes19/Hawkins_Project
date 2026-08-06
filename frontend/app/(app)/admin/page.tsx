"use client";

import { useState } from "react";
import { useAuth } from "@/context/auth-context";
import { DepartmentsTab } from "@/components/admin/DepartmentsTab";
import { UsersTab } from "@/components/admin/UsersTab";
import { FilesTab } from "@/components/admin/FilesTab";

const TABS = ["Files", "Users", "Departments"] as const;

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Files");

  if (user && user.role !== "admin") {
    return (
      <div className="max-w-xl mx-auto px-6 py-16 text-center">
        <p className="text-sm text-danger">You do not have permission to view this page.</p>
        <p className="text-xs text-ink-faint mt-1">
          Contact your administrator if you believe this is a mistake.
        </p>
      </div>
    );
  }

  return (
    <div className="w-[92%] max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <h1 className="text-xl font-semibold text-ink mb-1">Admin Control Panel</h1>
      <p className="text-sm text-ink-muted mb-6">Signed in as {user?.username}</p>

      <div className="flex gap-1 border-b border-border mb-6">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`text-sm font-medium px-4 py-2.5 border-b-2 -mb-px transition-colors ${
              tab === t ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Files" && <FilesTab />}
      {tab === "Users" && <UsersTab />}
      {tab === "Departments" && <DepartmentsTab />}
    </div>
  );
}
