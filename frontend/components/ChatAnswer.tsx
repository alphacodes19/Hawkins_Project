"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { streamChat } from "@/lib/api";
import type { DocResult, FaithfulnessResult } from "@/lib/types";

interface Props {
  question: string;
  docs: DocResult[];
  /** Bubbles the finished answer up so the parent can cache it per-query,
   *  mirroring app.py's session_state[answer_key] cache — a repeat view of
   *  the same query shouldn't re-call the LLM. */
  onComplete: (result: { answer: string; faithfulness: FaithfulnessResult | null }) => void;
  cached?: { answer: string; faithfulness: FaithfulnessResult | null };
}

export function ChatAnswer({ question, docs, onComplete, cached }: Props) {
  const [answer, setAnswer] = useState(cached?.answer ?? "");
  const [streaming, setStreaming] = useState(!cached);
  const [faithfulness, setFaithfulness] = useState<FaithfulnessResult | null>(
    cached?.faithfulness ?? null
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cached) return;

    const controller = new AbortController();
    // Scoped to THIS invocation of the effect via closure — not a ref, which
    // would persist across React 18 Strict Mode's dev-mode double-invocation
    // (mount → cleanup → mount) and incorrectly block the second, real fetch
    // from ever starting. That was the actual bug behind three straight
    // reports of "no answer generated": the phantom first invocation started
    // the only fetch that ever ran, then Strict Mode's cleanup aborted it,
    // and the second invocation saw a stale guard and skipped starting a
    // replacement. A local variable is fresh on every invocation, so this
    // can't happen — the phantom run cancels cleanly, the real run proceeds.
    let cancelled = false;

    setStreaming(true);
    setAnswer("");
    setError(null);
    setFaithfulness(null);

    (async () => {
      let gotTerminalEvent = false;
      try {
        for await (const event of streamChat(question, docs, controller.signal)) {
          if (cancelled) break;
          if (event.type === "token") {
            setAnswer((prev) => prev + event.text);
          } else if (event.type === "done") {
            gotTerminalEvent = true;
            setFaithfulness(event.faithfulness);
            setStreaming(false);
            onComplete({ answer: event.answer, faithfulness: event.faithfulness });
          } else if (event.type === "error") {
            gotTerminalEvent = true;
            setError(event.message);
            setStreaming(false);
          }
        }
        if (!gotTerminalEvent && !cancelled) {
          // The connection closed without a "done" or "error" frame — treat
          // as a failure rather than leaving the UI stuck showing the
          // streaming cursor forever.
          setError("The connection closed before a response was received.");
          setStreaming(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to generate an answer.");
          setStreaming(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question]);

  return (
    <div className="rounded-lg border border-accent/25 bg-gradient-to-br from-accent-soft/70 to-surface shadow-card overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-accent/15 bg-accent-soft/40">
        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-accent text-white shrink-0">
          <Sparkles className="w-3.5 h-3.5" />
        </span>
        <h3 className="text-sm font-semibold text-accent-hover">AI Summary</h3>
        <span className="text-[10px] uppercase tracking-wide font-medium text-ink-faint bg-surface border border-border rounded-full px-2 py-0.5 ml-1">
          Generated
        </span>
        {streaming && (
          <span className="ml-auto flex items-center gap-1 text-xs text-ink-faint">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulseDot" />
            Generating…
          </span>
        )}
      </div>

      <div className="px-4 py-3.5">
        {error ? (
          <div className="text-sm text-danger bg-danger-soft border border-danger/20 rounded-md px-3.5 py-3">
            <p className="font-medium mb-1">Couldn&apos;t generate an answer</p>
            <p className="text-danger/90">{error}</p>
            <p className="text-xs text-danger/70 mt-2">
              If this keeps happening, check that Ollama is running and that the configured model is
              pulled locally (see the server terminal for the exact error).
            </p>
          </div>
        ) : (
          <div className="text-sm text-ink leading-relaxed whitespace-pre-wrap">
            {answer}
            {streaming && (
              <span className="inline-block w-1.5 h-4 bg-accent/70 ml-0.5 align-middle animate-pulseDot" />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
