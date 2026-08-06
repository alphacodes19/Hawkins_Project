#!/usr/bin/env python3
"""
eval/eval_retrieval.py — Retrieval Quality Evaluation
=======================================================
Measures recall@k for the hybrid pipeline vs. vector-only baseline,
using the labeled query→document pairs in eval/eval_set.json.

WHAT THIS MEASURES
------------------
Recall@k = fraction of queries where at least one expected source
           appears in the top-k retrieved documents.

A recall@5 of 0.75 means 75% of queries had the right document in
the top-5 results.

HOW TO USE
-----------
1. Index your documents:
       python -m pipeline.indexer --reset

2. Fill in expected_sources in eval/eval_set.json:
       For each query, add the exact source filename(s) as they appear
       in ChromaDB — e.g. "HR_Leave_Policy_2024.pdf"

3. Run this script:
       python eval/eval_retrieval.py

   Optional flags:
       --k 10               # evaluate recall@10 instead of @5
       --admin              # run as admin (no ACL filter)
       --category policy    # evaluate only queries in this category
       --output results.json  # save full results to JSON

OUTPUT
------
Prints a table like:

  Recall@5 — Hybrid pipeline vs. Vector-only baseline
  =====================================================
  Category      Queries  Hybrid  Vector-only  Delta
  -----------   -------  ------  -----------  -----
  policy             5    0.80         0.60  +0.20
  vendor             4    0.75         0.50  +0.25
  product_manual     6    0.83         0.67  +0.16
  ...
  ALL               28    0.79         0.61  +0.18

  ✓ Hybrid pipeline outperforms vector-only on 20/28 queries.

This number is what you put in your resume bullet:
  "Hybrid BM25+dense+rerank pipeline achieved recall@5 of 79%,
   vs 61% for vector-only (+18pp improvement)"

NOTES
-----
- Queries with empty expected_sources are skipped (labelled as UNLABELLED).
- The script prints each query's result so you can spot bad labels quickly.
- Vector-only baseline uses retrieve() in retriever.py (no BM25, no rerank).
- Hybrid uses retrieve_documents() — the full pipeline.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

# ── Project root on path ──────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

import config  # noqa: E402  (needs project root first)


def _load_eval_set():
    path = os.path.join(SCRIPT_DIR, "eval_set.json")
    with open(path) as f:
        data = json.load(f)
    return data["queries"]


def _eval_hybrid(query, k, allowed):
    """Run the full hybrid pipeline and return retrieved source names."""
    from retrieval.retriever import retrieve_documents
    docs, _ = retrieve_documents(
        query,
        top_n_docs=k,
        use_reranker=True,
        allowed_doc_ids=allowed,
    )
    return [d["source"] for d in docs[:k]]


def _eval_vector_only(query, k, allowed):
    """Run the vector-only baseline and return retrieved source names."""
    from retrieval.retriever import retrieve
    chunks = retrieve(query, top_k=k * 3, allowed_doc_ids=allowed)
    # Deduplicate by source, preserving order
    seen, sources = set(), []
    for c in chunks:
        src = c["source"]
        if src not in seen:
            seen.add(src)
            sources.append(src)
        if len(sources) >= k:
            break
    return sources


def _hit(retrieved_sources, expected_sources, k):
    """True if any expected source appears in the top-k retrieved sources."""
    expected_set = set(expected_sources)
    return any(s in expected_set for s in retrieved_sources[:k])


def _print_row(label, n, hybrid_hits, vector_hits, width=14):
    if n == 0:
        return
    h = hybrid_hits / n
    v = vector_hits / n
    delta = h - v
    sign  = "+" if delta >= 0 else ""
    print(f"  {label:<{width}}  {n:>7}  {h:>6.2f}  {v:>11.2f}  {sign}{delta:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Retrieval recall@k evaluation")
    parser.add_argument("--k",        type=int, default=5,    help="Cutoff for recall@k (default 5)")
    parser.add_argument("--admin",    action="store_true",    help="Run as admin (no ACL filter)")
    parser.add_argument("--category", type=str, default=None, help="Evaluate only this category")
    parser.add_argument("--output",   type=str, default=None, help="Save full results to this JSON file")
    parser.add_argument("--verbose",  action="store_true",    help="Print per-query results")
    args = parser.parse_args()

    k       = args.k
    allowed = None if args.admin else set()   # None = admin (no filter), set() = no-one

    # ── Warn if ACL is empty ──────────────────────────────────────────────────
    if not args.admin:
        print(
            "⚠  Running with an empty allowed set (no ACL user provided).\n"
            "   Most documents will be invisible. Use --admin to bypass ACL,\n"
            "   or pass a real user's allowed_doc_ids to test real-world recall.\n"
        )
        print("   Hint: to eval as admin, run:  python eval/eval_retrieval.py --admin\n")

    queries = _load_eval_set()
    if args.category:
        queries = [q for q in queries if q.get("category") == args.category]

    labelled = [q for q in queries if q["expected_sources"]]
    skipped  = [q for q in queries if not q["expected_sources"]]

    if skipped:
        print(f"ℹ  Skipping {len(skipped)} unlabelled queries "
              f"(add expected_sources to eval_set.json to include them).\n")

    if not labelled:
        print("✗  No labelled queries found. Fill in expected_sources in eval_set.json.")
        sys.exit(1)

    # ── Run evaluation ────────────────────────────────────────────────────────
    results      = []
    by_category  = defaultdict(lambda: {"n": 0, "hybrid": 0, "vector": 0})
    total_hybrid = 0
    total_vector = 0

    print(f"Running recall@{k} on {len(labelled)} labelled queries...\n")

    for q in labelled:
        t0 = time.time()

        hybrid_sources = _eval_hybrid(q["query"], k, allowed)
        hybrid_hit     = _hit(hybrid_sources, q["expected_sources"], k)

        vector_sources = _eval_vector_only(q["query"], k, allowed)
        vector_hit     = _hit(vector_sources, q["expected_sources"], k)

        elapsed = time.time() - t0

        result = {
            "id":             q["id"],
            "query":          q["query"],
            "category":       q.get("category", ""),
            "expected":       q["expected_sources"],
            "hybrid_sources": hybrid_sources,
            "vector_sources": vector_sources,
            "hybrid_hit":     hybrid_hit,
            "vector_hit":     vector_hit,
            "elapsed_s":      round(elapsed, 1),
        }
        results.append(result)

        cat = q.get("category", "other")
        by_category[cat]["n"]      += 1
        by_category[cat]["hybrid"] += int(hybrid_hit)
        by_category[cat]["vector"] += int(vector_hit)
        total_hybrid += int(hybrid_hit)
        total_vector += int(vector_hit)

        if args.verbose:
            h_mark = "✓" if hybrid_hit else "✗"
            v_mark = "✓" if vector_hit else "✗"
            print(f"  [{q['id']}] {h_mark}H {v_mark}V  ({elapsed:.1f}s)  {q['query'][:60]}")

    # ── Print summary table ────────────────────────────────────────────────────
    n_total = len(labelled)
    print(f"\n  Recall@{k} — Hybrid pipeline vs. Vector-only baseline")
    print(f"  {'=' * 57}")
    print(f"  {'Category':<14}  {'Queries':>7}  {'Hybrid':>6}  {'Vector-only':>11}  {'Delta':>5}")
    print(f"  {'-'*14}  {'-'*7}  {'-'*6}  {'-'*11}  {'-'*5}")

    for cat in sorted(by_category):
        d = by_category[cat]
        _print_row(cat, d["n"], d["hybrid"], d["vector"])

    print(f"  {'-'*14}  {'-'*7}  {'-'*6}  {'-'*11}  {'-'*5}")
    _print_row("ALL", n_total, total_hybrid, total_vector)

    # ── Summary sentence ───────────────────────────────────────────────────────
    n_hybrid_wins = sum(1 for r in results if r["hybrid_hit"] and not r["vector_hit"])
    n_vector_wins = sum(1 for r in results if r["vector_hit"] and not r["hybrid_hit"])

    print(f"\n  Hybrid wins (hybrid hit, vector missed): {n_hybrid_wins}/{n_total}")
    print(f"  Vector wins (vector hit, hybrid missed): {n_vector_wins}/{n_total}")

    if total_hybrid > total_vector:
        print(f"\n  ✓ Hybrid pipeline outperforms vector-only on recall@{k}.")
    elif total_hybrid == total_vector:
        print(f"\n  ≈ Hybrid and vector-only are tied on recall@{k}.")
    else:
        print(f"\n  ✗ Vector-only outperforms hybrid on recall@{k} — investigate.")

    # ── Resume bullet helper ───────────────────────────────────────────────────
    hybrid_pct = total_hybrid / n_total * 100
    vector_pct = total_vector / n_total * 100
    delta_pp   = hybrid_pct - vector_pct
    print(
        f"\n  Resume bullet (fill in actual numbers):\n"
        f'  "Hybrid BM25+dense+rerank pipeline achieved recall@{k} of {hybrid_pct:.0f}%,\n'
        f'   vs {vector_pct:.0f}% for vector-only (+{delta_pp:.0f}pp improvement on {n_total} labelled queries)"\n'
    )

    # ── Save full results ──────────────────────────────────────────────────────
    if args.output:
        output_path = args.output
        with open(output_path, "w") as f:
            json.dump({
                "k":            k,
                "n_labelled":   n_total,
                "recall_hybrid": total_hybrid / n_total,
                "recall_vector": total_vector / n_total,
                "results":      results,
            }, f, indent=2)
        print(f"  Full results saved to: {output_path}")


if __name__ == "__main__":
    main()
