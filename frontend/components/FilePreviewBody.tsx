"use client";

import { filesApi } from "@/lib/api";
import { EmailCard } from "./EmailCard";
import { DocxPreview } from "./DocxPreview";

export const PREVIEWABLE_INLINE = new Set(["pdf", "jpg", "jpeg", "png", "gif", "webp", "txt", "md", "docx"]);
export const EMAIL_EXT = new Set(["eml", "emlx", "msg", "mbox"]);
export const IMAGE_EXT = new Set(["jpg", "jpeg", "png", "gif", "webp"]);

export function isPreviewable(source: string): boolean {
  const ext = source.includes(".") ? source.split(".").pop()!.toLowerCase() : "";
  return PREVIEWABLE_INLINE.has(ext) || EMAIL_EXT.has(ext);
}

/**
 * The actual preview content, with no modal/page chrome around it — shared
 * by FileViewerModal (popup), app/view (dedicated tab), AND email
 * attachment previews (a PDF/image attached to an email reuses this exact
 * same rendering path). That reuse is *why* viewUrl/downloadUrl are passed
 * in explicitly rather than always derived from docId internally: a normal
 * indexed document's URLs come from /api/files/view|download?doc_id=, but
 * an email attachment's come from /api/files/email-attachment/view|download
 * — same preview logic, different backend route, so the component needs to
 * not care which one it's pointed at. docId is still needed separately,
 * but only for the email case, which fetches its own structured data
 * (/api/files/email) rather than rendering a URL directly.
 */
export function FilePreviewBody({
  docId,
  source,
  viewUrl,
  downloadUrl,
}: {
  docId?: string;
  source: string;
  viewUrl: string;
  downloadUrl: string;
}) {
  const ext = source.includes(".") ? source.split(".").pop()!.toLowerCase() : "";

  if (ext === "pdf") {
    return <iframe src={viewUrl} title={source} className="w-full h-full rounded-md border border-border" />;
  }

  if (IMAGE_EXT.has(ext)) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={viewUrl} alt={source} className="max-w-full max-h-full object-contain rounded-md" />
      </div>
    );
  }

  if (ext === "txt" || ext === "md") {
    return (
      <iframe
        src={viewUrl}
        title="Text preview"
        className="w-full h-full rounded-md border border-border bg-canvas font-mono text-xs"
      />
    );
  }

  if (ext === "docx") {
    return <DocxPreview url={viewUrl} />;
  }

  if (EMAIL_EXT.has(ext) && docId) {
    return <EmailCard docId={docId} mode="full" />;
  }

  return (
    <div className="h-full flex flex-col items-center justify-center text-center gap-2">
      <p className="text-sm text-ink-muted">.{ext.toUpperCase()} files can&apos;t be previewed in-app yet.</p>
      <a
        href={downloadUrl}
        className="text-sm font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-3 py-1.5 mt-1"
      >
        Download to view
      </a>
    </div>
  );
}

/** Convenience for the common case: preview a normal indexed document by
 *  doc_id, deriving its view/download URLs the standard way. */
export function docFilePreviewProps(docId: string, source: string) {
  return { docId, source, viewUrl: filesApi.viewUrl(docId), downloadUrl: filesApi.downloadUrl(docId) };
}
