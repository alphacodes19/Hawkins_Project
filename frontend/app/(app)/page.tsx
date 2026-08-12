"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { History, Sparkles, ChevronDown } from "lucide-react";
import { SearchBar } from "@/components/SearchBar";
import { DocumentResults } from "@/components/DocumentResults";
import { ChatAnswer } from "@/components/ChatAnswer";
import { searchApi, ApiError } from "@/lib/api";
import type { Coverage, DocResult, FaithfulnessResult } from "@/lib/types";

interface CachedResult {
  docs: DocResult[];
  coverage: Coverage;
}
interface CachedAnswer {
  answer: string;
  faithfulness: FaithfulnessResult | null;
}

export default function SearchPage() {
  const [activeQuery, setActiveQuery] = useState("");
  const [sessionQueries, setSessionQueries] = useState<string[]>([]);
  const [resultsCache, setResultsCache] = useState<Record<string, CachedResult>>({});
  const [answerCache, setAnswerCache] = useState<Record<string, CachedAnswer>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Both default to matching current behaviour except where explicitly
  // requested otherwise: summary generation defaults ON (existing
  // behaviour preserved), previous-search preview defaults OFF (new ask).
  const [generateSummary, setGenerateSummary] = useState(true);
  const [showPrevious, setShowPrevious] = useState(false);

  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = useRef(new Date().toISOString());
  const sessionStart = useRef(new Date());

  const runSearch = useCallback(
    async (query: string, opts: { updateUrl?: boolean } = { updateUrl: true }) => {
      setActiveQuery(query);
      setError(null);
      if (opts.updateUrl) {
        router.replace(`/?q=${encodeURIComponent(query)}`, { scroll: false });
      }

      if (!resultsCache[query]) {
        setLoading(true);
        try {
          const { docs, coverage } = await searchApi.search(query, 20);
          setResultsCache((prev) => ({ ...prev, [query]: { docs, coverage } }));
          setSessionQueries((prev) => [query, ...prev.filter((q) => q !== query)].slice(0, 20));
          // Fire-and-forget history log — never block the search UI on this.
          searchApi
            .logQuery(sessionId.current, query, sessionStart.current.toISOString())
            .catch(() => {});
        } catch (err) {
          setError(err instanceof ApiError ? err.message : "Search failed. Try again.");
        } finally {
          setLoading(false);
        }
      } else {
        setSessionQueries((prev) => [query, ...prev.filter((q) => q !== query)].slice(0, 20));
      }
    },
    [resultsCache, router]
  );

  // Reacts to ?q= changes — covers both a direct link/refresh with a query
  // param already set, and HistoryPanel navigating here from the sidebar
  // while already on this page (App Router doesn't remount on that nav).
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && q !== activeQuery) {
      runSearch(q, { updateUrl: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  function handleClear() {
    setActiveQuery("");
    setError(null);
    router.replace("/", { scroll: false });
  }

  const prevQueries = sessionQueries.filter((q) => q !== activeQuery);

  return (
    <div className="w-[92%] max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-10 py-8">
      <h1 className="text-xl font-semibold text-ink mb-6">Hawkins Data Archive</h1>

      <SearchBar onSearch={runSearch} onClear={handleClear} initialValue={activeQuery} />

      <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-3">
        <label className="flex items-center gap-1.5 text-xs text-ink-muted cursor-pointer">
          <input
            type="checkbox"
            checked={generateSummary}
            onChange={(e) => setGenerateSummary(e.target.checked)}
            className="rounded border-border text-accent focus:ring-accent"
          />
          Generate AI summary
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-muted cursor-pointer">
          <input
            type="checkbox"
            checked={showPrevious}
            onChange={(e) => setShowPrevious(e.target.checked)}
            className="rounded border-border text-accent focus:ring-accent"
          />
          Show previous search
        </label>
      </div>

      <div className="mt-8 space-y-6">
        {error && <p className="text-sm text-danger">{error}</p>}

        {loading && !resultsCache[activeQuery] && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-14 rounded-lg bg-surface border border-border animate-pulse" />
            ))}
          </div>
        )}

        {activeQuery && resultsCache[activeQuery] && (
          <>
            <DocumentResults
              docs={resultsCache[activeQuery].docs}
              coverage={resultsCache[activeQuery].coverage}
            />
            {generateSummary && (
              <div className="border-t border-border pt-5">
                <ChatAnswer
                  question={activeQuery}
                  docs={resultsCache[activeQuery].docs}
                  cached={answerCache[activeQuery]}
                  onComplete={(result) =>
                    setAnswerCache((prev) => ({ ...prev, [activeQuery]: result }))
                  }
                />
              </div>
            )}
          </>
        )}

        {showPrevious && prevQueries.length > 0 && (
          <div className="border-t border-border pt-5">
            <div className="flex items-center gap-1.5 mb-3">
              <History className="w-4 h-4 text-ink-faint" />
              <h2 className="text-sm font-semibold text-ink">Previous searches</h2>
            </div>
            <div className="space-y-2">
              {prevQueries.map((q) => (
                <PreviousSearch
                  key={q}
                  query={q}
                  cached={resultsCache[q]}
                  cachedAnswer={answerCache[q]}
                  onReopen={() => runSearch(q)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function PreviousSearch({
  query,
  cached,
  cachedAnswer,
  onReopen,
}: {
  query: string;
  cached?: CachedResult;
  cachedAnswer?: CachedAnswer;
  onReopen: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-border rounded-lg bg-canvas/40">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-4 py-2.5 text-sm text-ink-muted hover:text-ink flex items-center justify-between"
      >
        <span className="truncate flex items-center gap-2">
          <History className="w-3.5 h-3.5 text-ink-faint shrink-0" />
          {query}
        </span>
        <ChevronDown className={`w-4 h-4 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="px-4 pb-4 border-t border-border pt-3 space-y-4 animate-fadeIn bg-surface rounded-b-lg">
          {cached ? (
            <>
              <DocumentResults docs={cached.docs} coverage={cached.coverage} />
              {cachedAnswer && (
                <div className="rounded-lg border border-accent/20 bg-accent-soft/40 overflow-hidden">
                  <div className="flex items-center gap-1.5 px-3.5 py-2 border-b border-accent/10">
                    <Sparkles className="w-3.5 h-3.5 text-accent" />
                    <h3 className="text-xs font-semibold text-accent-hover">AI Summary</h3>
                  </div>
                  <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap px-3.5 py-3">
                    {cachedAnswer.answer}
                  </p>
                </div>
              )}
            </>
          ) : (
            <button onClick={onReopen} className="text-sm text-accent hover:text-accent-hover">
              Search again: {query}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
