"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { searchApi } from "@/lib/api";
import { ConfirmDialog } from "./ConfirmDialog";
import type { SearchHistorySession } from "@/lib/types";

export function HistoryPanel() {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<SearchHistorySession[] | null>(null);
  const router = useRouter();

  useEffect(() => {
    if (open && sessions === null) {
      searchApi.history().then(setSessions).catch(() => setSessions([]));
    }
  }, [open, sessions]);

  function goToQuery(q: string) {
    router.push(`/?q=${encodeURIComponent(q)}`);
  }

  async function handleDelete(entryId: number) {
    // Remove locally after the request succeeds, and drop any session
    // that's left with zero queries so it disappears from the list rather
    // than lingering as an empty group.
    await searchApi.deleteHistoryEntry(entryId);
    setSessions((prev) =>
      (prev ?? [])
        .map((s) => ({ ...s, queries: s.queries.filter((q) => q.id !== entryId) }))
        .filter((s) => s.queries.length > 0)
    );
  }

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between text-sm text-ink-muted hover:text-ink px-3 py-2 rounded-md hover:bg-canvas transition-colors"
      >
        <span>Search History</span>
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {open && (
        <div className="px-1 pb-2 max-h-72 overflow-y-auto scrollbar-thin">
          {sessions === null ? (
            <p className="text-xs text-ink-faint px-2 py-1">Loading…</p>
          ) : sessions.length === 0 ? (
            <p className="text-xs text-ink-faint px-2 py-1">No search history yet.</p>
          ) : (
            sessions
              .slice(0, 30)
              .map((s) => <SessionGroup key={s.session_id} session={s} onPick={goToQuery} onDelete={handleDelete} />)
          )}
        </div>
      )}
    </div>
  );
}

function SessionGroup({
  session,
  onPick,
  onDelete,
}: {
  session: SearchHistorySession;
  onPick: (q: string) => void;
  onDelete: (entryId: number) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-t border-border first:border-t-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-2 py-1.5 text-xs text-ink-muted hover:text-ink"
      >
        {session.date_label} ({session.start_time}) — {session.queries.length} search
        {session.queries.length === 1 ? "" : "es"}
      </button>
      {open && (
        <div className="pb-1.5 space-y-0.5">
          {session.queries.map((q) => (
            <HistoryEntryRow key={q.id} id={q.id} query={q.query} onPick={onPick} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  );
}

function HistoryEntryRow({
  id,
  query,
  onPick,
  onDelete,
}: {
  id: number;
  query: string;
  onPick: (q: string) => void;
  onDelete: (entryId: number) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="group flex items-center px-3 py-1 rounded-md hover:bg-canvas">
      <button
        onClick={() => onPick(query)}
        className="flex-1 min-w-0 text-left text-xs text-ink-faint hover:text-accent truncate"
        title={query}
      >
        {query.length > 48 ? query.slice(0, 48) + "…" : query}
      </button>
      <button
        onClick={() => setConfirming(true)}
        title="Delete this search"
        aria-label="Delete this search"
        className="shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-ink-faint hover:text-danger p-0.5 rounded transition-opacity"
      >
        <Trash2 className="w-3 h-3" />
      </button>

      {confirming && (
        <ConfirmDialog
          title="Delete this search?"
          message="This permanently removes it from your search history. This can't be undone."
          confirmLabel="Delete"
          danger
          onConfirm={() => {
            setConfirming(false);
            onDelete(id);
          }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
