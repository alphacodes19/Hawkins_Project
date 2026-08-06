"use client";

import { filesApi } from "@/lib/api";
import { FilePreviewBody } from "./FilePreviewBody";
import { useEscapeKey } from "@/lib/use-escape-key";
import { Portal } from "./Portal";

interface FileViewerModalProps {
  source: string;
  onClose: () => void;
  /** Normal case: a real indexed document. Provide this and viewUrl/downloadUrl
   *  are derived automatically, plus "Open in New Tab" becomes available. */
  docId?: string;
  /** Override case: anything NOT a standalone indexed doc — currently just
   *  email attachments, which live at a different backend route and have
   *  no /view?doc_id= equivalent to open in a new tab. */
  viewUrl?: string;
  downloadUrl?: string;
  /** Portals stack by DOM order at equal z-index; bump this for a modal
   *  opened from inside another modal (attachment preview from within the
   *  email popup) so it's unambiguously on top rather than relying on
   *  paint-order luck. */
  z?: number;
}

export function FileViewerModal({ docId, source, onClose, viewUrl, downloadUrl, z = 50 }: FileViewerModalProps) {
  useEscapeKey(onClose);

  const resolvedViewUrl = viewUrl ?? (docId ? filesApi.viewUrl(docId) : "");
  const resolvedDownloadUrl = downloadUrl ?? (docId ? filesApi.downloadUrl(docId) : "");
  const newTabUrl = docId ? `/view?doc_id=${encodeURIComponent(docId)}&name=${encodeURIComponent(source)}` : null;

  return (
    <Portal>
      <div
        className="fixed inset-0 bg-ink/50 flex items-center justify-center p-4"
        style={{ zIndex: z }}
        onClick={onClose}
      >
        <div
          className="bg-surface rounded-lg shadow-popover w-full max-w-6xl h-[88vh] flex flex-col animate-fadeIn"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
            <p className="text-sm font-medium text-ink truncate">{source}</p>
            <div className="flex items-center gap-2 shrink-0 ml-3">
              {newTabUrl && (
                <a
                  href={newTabUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-2.5 py-1"
                >
                  Open in New Tab
                </a>
              )}
              <a
                href={resolvedDownloadUrl}
                className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1"
              >
                Download
              </a>
              <button
                onClick={onClose}
                className="text-ink-faint hover:text-ink text-lg leading-none px-1"
                aria-label="Close preview"
              >
                ×
              </button>
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-auto p-4">
            <FilePreviewBody
              docId={docId}
              source={source}
              viewUrl={resolvedViewUrl}
              downloadUrl={resolvedDownloadUrl}
            />
          </div>
        </div>
      </div>
    </Portal>
  );
}
