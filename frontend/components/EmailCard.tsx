"use client";

import { useEffect, useState } from "react";
import { filesApi } from "@/lib/api";
import type { EmailPreview, EmailAttachment } from "@/lib/api";
import { formatBytes } from "@/lib/text-utils";
import { FileViewerModal } from "./FileViewerModal";
import { PREVIEWABLE_INLINE } from "./FilePreviewBody";

const PREVIEW_CHARS = 220;
const EXT_ICON: Record<string, string> = {
  pdf: "PDF", docx: "DOC", doc: "DOC", xlsx: "XLS", xls: "XLS",
};

function extOf(name: string) {
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

/**
 * Header fields (Subject/From/To/Cc/Date) always render in full — those are
 * short and useful to scan. The body is what gets truncated: in "compact"
 * mode (used inline in the results list) it shows a short lead-in with a
 * "Read more" button instead of a fixed-height scroll box, so the card
 * height stays predictable in a list of many results. "full" mode (used
 * inside the file viewer modal / dedicated tab) shows the entire body plus
 * the attachment list, no truncation.
 */
export function EmailCard({
  docId,
  mode = "compact",
  onReadMore,
}: {
  docId: string;
  mode?: "compact" | "full";
  onReadMore?: () => void;
}) {
  const [email, setEmail] = useState<EmailPreview | null>(null);
  const [error, setError] = useState(false);
  const [previewAttachment, setPreviewAttachment] = useState<{ att: EmailAttachment; index: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    filesApi
      .emailPreview(docId)
      .then((e) => !cancelled && setEmail(e))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (error) return null; // silent fallback, same as app.py's try/except pass
  if (!email) {
    return <div className="h-16 rounded-md bg-canvas animate-pulse" />;
  }

  const isTruncated = mode === "compact" && email.body.length > PREVIEW_CHARS;
  const bodyToShow = isTruncated ? email.body.slice(0, PREVIEW_CHARS).trimEnd() + "…" : email.body;

  return (
    <div>
      <div className="border-t border-b border-border py-2.5 mb-3">
        {email.subject && <p className="text-sm font-semibold text-ink mb-1.5">{email.subject}</p>}
        <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-xs text-ink-muted">
          {email.from && (
            <>
              <span className="text-ink-faint">From:</span>
              <span className="truncate">{email.from}</span>
            </>
          )}
          {email.to && (
            <>
              <span className="text-ink-faint">To:</span>
              <span className="truncate">{email.to}</span>
            </>
          )}
          {email.cc && (
            <>
              <span className="text-ink-faint">Cc:</span>
              <span className="truncate">{email.cc}</span>
            </>
          )}
          {email.date && (
            <>
              <span className="text-ink-faint">Date:</span>
              <span>{email.date}</span>
            </>
          )}
        </div>
      </div>

      <div className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink">{bodyToShow}</div>

      {isTruncated && (
        <button
          onClick={onReadMore}
          className="mt-2 text-xs font-medium text-accent hover:text-accent-hover"
        >
          Read more →
        </button>
      )}

      {mode === "compact" && email.has_attachments && !isTruncated && (
        <span className="inline-flex items-center gap-1 text-xs text-ink-muted bg-canvas border border-border rounded-full px-2 py-0.5 mt-2">
          📎 {email.attachments.length} attachment{email.attachments.length === 1 ? "" : "s"}
        </span>
      )}

      {mode === "full" && email.attachments.length > 0 && (
        <div className="border-t border-border mt-4 pt-3">
          <p className="text-xs font-semibold text-ink mb-2">
            Attachments ({email.attachments.length})
          </p>
          <div className="space-y-1.5">
            {email.attachments.map((att, i) => {
              const ext = extOf(att.filename);
              const badge = EXT_ICON[ext] ?? (ext.toUpperCase() || "FILE");
              const canPreview = PREVIEWABLE_INLINE.has(ext);
              return (
                <div
                  key={i}
                  className="flex items-center gap-2.5 border border-border rounded-md px-3 py-2 bg-canvas/40"
                >
                  <span className="shrink-0 font-mono text-[10px] font-semibold text-ink-muted bg-surface border border-border rounded px-1.5 py-0.5">
                    {badge}
                  </span>
                  <span className="flex-1 min-w-0 text-sm text-ink truncate">📎 {att.filename}</span>
                  <span className="shrink-0 text-xs text-ink-faint">{formatBytes(att.size)}</span>
                  {canPreview && (
                    <button
                      onClick={() => setPreviewAttachment({ att, index: i })}
                      className="shrink-0 text-xs font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-2.5 py-1"
                    >
                      View
                    </button>
                  )}
                  <a
                    href={filesApi.emailAttachmentDownloadUrl(docId, i)}
                    className="shrink-0 text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1"
                  >
                    Download
                  </a>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {previewAttachment && (
        <FileViewerModal
          source={previewAttachment.att.filename}
          viewUrl={filesApi.emailAttachmentViewUrl(docId, previewAttachment.index)}
          downloadUrl={filesApi.emailAttachmentDownloadUrl(docId, previewAttachment.index)}
          onClose={() => setPreviewAttachment(null)}
          z={60}
        />
      )}
    </div>
  );
}
