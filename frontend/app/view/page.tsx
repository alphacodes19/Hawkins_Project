"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { FilePreviewBody } from "@/components/FilePreviewBody";
import { filesApi } from "@/lib/api";

/**
 * Deliberately its own route outside the (app) group — no sidebar, no
 * search chrome, just the document. Opened via target="_blank" from the
 * viewer modal's "Open in New Tab", so the original tab's search results
 * and state are completely untouched; each tab opened this way is
 * independent, letting several documents be compared side by side.
 */
function ViewPageContent() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc_id");
  const name = searchParams.get("name") ?? "Document";

  useEffect(() => {
    if (!loading && !user) router.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <div className="w-2 h-2 rounded-full bg-ink-faint animate-pulseDot" />
      </div>
    );
  }

  if (!user) return null;

  if (!docId) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas px-4">
        <p className="text-sm text-ink-muted">No document specified.</p>
      </div>
    );
  }

  return (
    <div className="h-screen bg-canvas flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-surface shrink-0">
        <p className="text-sm font-medium text-ink truncate">{name}</p>
        <a
          href={filesApi.downloadUrl(docId)}
          className="text-xs font-medium text-ink-muted hover:text-ink border border-border rounded-md px-2.5 py-1 shrink-0 ml-3"
        >
          Download
        </a>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        <FilePreviewBody
          docId={docId}
          source={name}
          viewUrl={filesApi.viewUrl(docId)}
          downloadUrl={filesApi.downloadUrl(docId)}
        />
      </div>
    </div>
  );
}

export default function ViewPage() {
  // useSearchParams needs a Suspense boundary for the build; the fallback
  // is instant in practice since this page has no data to prefetch.
  return (
    <Suspense fallback={null}>
      <ViewPageContent />
    </Suspense>
  );
}
