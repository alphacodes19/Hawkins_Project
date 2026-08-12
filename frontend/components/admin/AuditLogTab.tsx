"use client";

import { useEffect, useState } from "react";
import { ScrollText, ChevronDown } from "lucide-react";
import { adminApi, ApiError } from "@/lib/api";
import type { AuditLogEntry } from "@/lib/types";

export function AuditLogTab() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const rows = await adminApi.auditLog({
        actor: actor || undefined,
        action: action || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setEntries(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the audit log.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    adminApi.auditLogActions().then(setActions).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actor, action, dateFrom, dateTo]);

  const distinctActors = [...new Set(entries.map((e) => e.actor_username))].sort();

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-1">
        <ScrollText className="w-4 h-4 text-ink-muted" />
        <h2 className="text-sm font-semibold text-ink">Audit Log</h2>
      </div>
      <p className="text-xs text-ink-faint -mt-3">
        Every admin action that changes state — who did what, when, and (where relevant) what changed.
        This log can't be edited or deleted from here.
      </p>

      <div className="flex flex-wrap items-end gap-2.5 bg-canvas/50 border border-border rounded-lg p-3">
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">Admin</label>
          <input
            list="audit-actors"
            placeholder="Any"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5 w-36"
          />
          <datalist id="audit-actors">
            {distinctActors.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">Action</label>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5 bg-surface"
          >
            <option value="">Any</option>
            {actions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-muted mb-1">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="text-sm rounded-md border border-border px-2.5 py-1.5"
          />
        </div>
        {(actor || action || dateFrom || dateTo) && (
          <button
            onClick={() => {
              setActor("");
              setAction("");
              setDateFrom("");
              setDateTo("");
            }}
            className="text-xs text-ink-faint hover:text-ink underline mb-1.5"
          >
            Clear filters
          </button>
        )}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-11 rounded-lg bg-surface border border-border animate-pulse" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-ink-muted">No audit entries match these filters.</p>
      ) : (
        <div className="space-y-1.5">
          {entries.map((e) => (
            <AuditRow key={e.id} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditLogEntry }) {
  const [open, setOpen] = useState(false);
  const hasDetail = entry.before || entry.after;

  return (
    <div className="border border-border rounded-lg bg-surface">
      <button
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left ${hasDetail ? "" : "cursor-default"}`}
      >
        <div className="min-w-0">
          <p className="text-sm text-ink truncate">{entry.description}</p>
          <p className="text-xs text-ink-faint">
            {entry.actor_username} · {new Date(entry.created_at).toLocaleString()} ·{" "}
            <span className="font-mono">{entry.action}</span>
          </p>
        </div>
        {hasDetail && (
          <ChevronDown className={`w-4 h-4 shrink-0 text-ink-faint transition-transform ${open ? "rotate-180" : ""}`} />
        )}
      </button>
      {open && hasDetail && (
        <div className="px-4 pb-3 border-t border-border pt-2.5 animate-fadeIn grid grid-cols-2 gap-3 text-xs">
          {entry.before && (
            <div>
              <p className="font-medium text-ink-muted mb-1">Before</p>
              <pre className="font-mono text-ink-faint bg-canvas rounded-md p-2 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(entry.before, null, 2)}
              </pre>
            </div>
          )}
          {entry.after && (
            <div>
              <p className="font-medium text-ink-muted mb-1">After</p>
              <pre className="font-mono text-ink-faint bg-canvas rounded-md p-2 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(entry.after, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
