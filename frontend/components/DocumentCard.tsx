"use client";

import { useState, memo } from "react";
import type { DocResult } from "@/lib/types";
import { RelevanceBar } from "./RelevanceBar";
import { EmailCard } from "./EmailCard";
import { FileViewerModal } from "./FileViewerModal";
import { isGarbled, bestReadableChunk } from "@/lib/text-utils";
import { filesApi } from "@/lib/api";
import { formatBytes } from "@/lib/text-utils";

const FILE_ICON: Record<string, string> = {
  pdf: "PDF",
  docx: "DOC",
  doc: "DOC",
  xlsx: "XLS",
  xls: "XLS",
  eml: "MAIL",
  msg: "MAIL",
  mbox: "MAIL",
};

const EMAIL_EXT = new Set(["eml", "emlx", "msg", "mbox"]);

export const DocumentCard = memo(function DocumentCard({
  doc,
  defaultOpen,
}: {
  doc: DocResult;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [showViewer, setShowViewer] = useState(false);
  const ext = doc.source.includes(".") ? doc.source.split(".").pop()!.toLowerCase() : "";
  const badge = FILE_ICON[ext] ?? (ext.toUpperCase() || "DOC");
  const isEmail =
    EMAIL_EXT.has(ext) ||
    ["email", "email_msg", "email_mbox", "email_emlx"].includes(doc.source_type || "");

  const best = doc.matched_chunks.length ? bestReadableChunk(doc.matched_chunks) : null;
  const garbled = best ? isGarbled(best.text) : false;

  const metaParts = [doc.department && `Dept: ${doc.department}`, doc.date, doc.summary].filter(
    Boolean
  ) as string[];

  return (
    <div className="border border-border rounded-lg bg-surface overflow-hidden transition-shadow hover:shadow-card">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-canvas/60 transition-colors"
        aria-expanded={open}
      >
        <span className="shrink-0 font-mono text-[10px] font-semibold tracking-wide text-ink-muted bg-canvas border border-border rounded px-1.5 py-0.5">
          {badge}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-ink truncate">{doc.source}</span>
        {typeof doc.file_size === "number" && (
          <span className="shrink-0 text-xs text-ink-faint font-mono">{formatBytes(doc.file_size)}</span>
        )}
        <RelevanceBar pct={doc.relevance_pct} />
        <svg
          className={`shrink-0 w-4 h-4 text-ink-faint transition-transform duration-200 ${open ? "rotate-180" : ""}`}
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
        <div className="px-4 pb-4 pt-1 border-t border-border animate-fadeIn">
          {metaParts.length > 0 && <p className="text-xs text-ink-muted mb-2">{metaParts.join(" · ")}</p>}

          {isEmail && doc.doc_id ? (
            <EmailCard docId={doc.doc_id} mode="compact" onReadMore={() => setShowViewer(true)} />
          ) : (
            <>
              <p className="text-xs text-ink-faint mb-2">
                {doc.matched_chunks.length} matching section{doc.matched_chunks.length === 1 ? "" : "s"}
                {best?.page ? ` · page ${best.page}` : ""}
                {best?.ocr === "true" ? " · OCR" : ""}
              </p>

              {garbled ? (
                <p className="text-sm text-ink-muted italic">
                  Text preview unavailable — poor quality scan. Use Download to read this file.
                </p>
              ) : (
                best && (
                  <p className="text-sm text-ink-muted leading-relaxed whitespace-pre-wrap font-normal max-w-3xl">
                    {best.text.slice(0, 400)}
                    {best.text.length >= 400 ? "…" : ""}
                  </p>
                )
              )}
            </>
          )}

          <div className="flex gap-2 mt-3">
            {doc.doc_id && (
              <>
                <button
                  onClick={() => setShowViewer(true)}
                  className="text-xs font-medium text-accent hover:text-accent-hover border border-accent/30 rounded-md px-2.5 py-1 transition-colors"
                >
                  View
                </button>
                <a
                  href={filesApi.downloadUrl(doc.doc_id)}
                  className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1 transition-colors"
                >
                  Download
                </a>
              </>
            )}
          </div>
        </div>
      )}

      {showViewer && doc.doc_id && (
        <FileViewerModal docId={doc.doc_id} source={doc.source} onClose={() => setShowViewer(false)} />
      )}
    </div>
  );
});
