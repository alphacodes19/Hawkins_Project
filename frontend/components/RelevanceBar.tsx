/**
 * Visual encoding of relevance_pct, which the backend assigns by RANK
 * POSITION (see retrieval/retriever.py) rather than raw score — so the bar
 * length always matches the document's position in the list. A number alone
 * ("87% match") makes you read every card to compare; a bar lets you scan
 * the whole result set's shape in one glance.
 */
export function RelevanceBar({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2 shrink-0" aria-label={`${pct}% relevance`}>
      <div className="w-14 h-1.5 rounded-full bg-border overflow-hidden">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${Math.max(4, Math.min(100, pct))}%` }}
        />
      </div>
      <span className="text-xs font-mono text-ink-muted tabular-nums w-8 text-right">{pct}%</span>
    </div>
  );
}
