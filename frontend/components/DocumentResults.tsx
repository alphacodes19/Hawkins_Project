import type { Coverage, DocResult } from "@/lib/types";
import { DocumentCard } from "./DocumentCard";
import { AdditionalKeywordMatches } from "./AdditionalKeywordMatches";

export function DocumentResults({
  docs,
  coverage,
}: {
  docs: DocResult[];
  coverage?: Coverage;
}) {
  if (!docs.length && (!coverage || coverage.keyword_file_count === 0)) {
    return (
      <div className="text-sm text-ink-muted bg-surface border border-border rounded-lg px-4 py-3">
        No documents found for this query.
      </div>
    );
  }

  const shownSources = new Set(docs.map((d) => d.source));

  return (
    <div className="space-y-3">
      {coverage && (
        <div className="text-sm text-ink-muted bg-accent-soft/50 border border-accent/15 rounded-lg px-4 py-2.5">
          {coverage.keyword_file_count > 0 ? (
            <>
              Showing <span className="font-medium text-ink">top {docs.length} documents</span>{" "}
              ranked by relevance · Exact keyword found in{" "}
              <span className="font-medium text-ink">{coverage.keyword_file_count} files</span> you
              can access ({coverage.keyword_chunk_count} total sections)
            </>
          ) : (
            <>
              Showing <span className="font-medium text-ink">top {docs.length} documents</span>{" "}
              ranked by semantic relevance · No exact keyword match — results are based on meaning
            </>
          )}
        </div>
      )}

      <div className="space-y-2">
        {docs.map((doc, i) => (
          <DocumentCard key={doc.doc_id || doc.source} doc={doc} defaultOpen={i < 3} />
        ))}
      </div>

      {coverage && coverage.keyword_file_count > docs.length && (
        <AdditionalKeywordMatches keywordSources={coverage.keyword_sources} shownSources={shownSources} />
      )}
    </div>
  );
}
