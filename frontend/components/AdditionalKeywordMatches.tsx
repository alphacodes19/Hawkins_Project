"use client";

import { useEffect, useState } from "react";
import { searchApi, filesApi } from "@/lib/api";
import type { ResolvedSource } from "@/lib/api";

/**
 * Restores app.py's "Show all N files containing this keyword (including
 * lower-ranked results)" expander (app.py ~line 866-958). Only rendered when
 * coverage says more keyword-matched files exist than are shown in the
 * ranked list above.
 */
export function AdditionalKeywordMatches({
  keywordSources,
  shownSources,
}: {
  keywordSources: Record<string, number>;
  shownSources: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const [resolved, setResolved] = useState<ResolvedSource[] | null>(null);

  const additional = Object.entries(keywordSources).filter(([src]) => !shownSources.has(src));
  const sorted = additional.sort((a, b) => b[1] - a[1]);

  useEffect(() => {
    if (open && !resolved && sorted.length) {
      searchApi.resolveSources(sorted.map(([src]) => src)).then(setResolved);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!sorted.length) return null;

  return (
    <div className="border border-border rounded-lg bg-surface">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-4 py-3 text-sm text-ink-muted hover:text-ink"
      >
        Show all {sorted.length + shownSources.size} files containing this keyword
        <span className="text-ink-faint"> (including lower-ranked results)</span>
      </button>

      {open && (
        <div className="px-4 pb-3 border-t border-border pt-2 divide-y divide-border animate-fadeIn">
          <p className="text-xs text-ink-faint pb-2">
            These files contain the exact keyword but ranked lower than the top results above.
          </p>
          {sorted.map(([src, count]) => {
            const r = resolved?.find((x) => x.source === src);
            return (
              <div key={src} className="flex items-center justify-between py-2 gap-3">
                <div className="min-w-0">
                  <p className="text-sm text-ink truncate">{src}</p>
                  <p className="text-xs text-ink-faint">{count} matching section(s)</p>
                </div>
                {r?.available && r.doc_id ? (
                  <a
                    href={filesApi.downloadUrl(r.doc_id)}
                    className="shrink-0 text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1"
                  >
                    Download
                  </a>
                ) : (
                  <span className="shrink-0 text-xs text-ink-faint">
                    {resolved ? "Not available" : "…"}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
