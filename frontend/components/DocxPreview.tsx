"use client";

import { useEffect, useState } from "react";

/**
 * Real DOCX → HTML preview, the follow-up to what was deferred earlier as
 * "needs a conversion library." mammoth converts docx's OOXML structure to
 * semantic HTML client-side — no server-side conversion step needed. Legacy
 * .doc (pre-2007 binary format) isn't supported by mammoth; those still fall
 * through to the "download to view" message, same as before.
 *
 * The fetched file goes through the same authenticated /api/files/view
 * endpoint as everything else (credentials: include), so this doesn't
 * bypass the ACL check that already gated showing the View button.
 */
export function DocxPreview({ url }: { url: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const mammoth = await import("mammoth");
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) throw new Error("Could not load the file.");
        const arrayBuffer = await res.arrayBuffer();
        const result = await mammoth.convertToHtml({ arrayBuffer });
        if (!cancelled) setHtml(result.value);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not render this file.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [url]);

  if (error) {
    return <p className="text-sm text-ink-muted text-center py-8">{error}</p>;
  }

  if (!html) {
    return (
      <div className="space-y-2 p-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-3 rounded bg-canvas animate-pulse" style={{ width: `${90 - i * 12}%` }} />
        ))}
      </div>
    );
  }

  return (
    <div
      className="docx-preview max-w-none bg-surface rounded-md border border-border p-6 text-sm text-ink leading-relaxed"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
