#!/usr/bin/env python3
"""
eval/build_eval_set.py — Fill in eval_set.json against your real corpus
=========================================================================
eval_set.json ships with 28 queries but empty expected_sources — nobody
can honestly fill those in except you, against your actual indexed
documents. This script doesn't guess for you; it makes the guessing fast
and always shows its work, so you're confirming/rejecting suggestions in
seconds instead of manually browsing ChromaDB for each of the 28 queries.

WHAT IT DOES
------------
1. Connects to your live ChromaDB (config.CHROMA_PATH / CHROMA_COLLECTION)
   and lists every unique indexed source filename + doc_type/department/
   project metadata (if metadata tagging was enabled when you indexed).
2. For each query in eval_set.json with empty expected_sources, scores
   every indexed filename by token overlap with the query text + category,
   and prints the top candidates ranked by score.
3. Never writes a file for you silently. Two modes:
     --dry-run (default)  Just prints suggestions. Nothing is written.
     --apply              Writes ONLY high-confidence matches (score above
                           --threshold) into eval_set.json. Everything else
                           is left empty with a "notes" field flagged
                           NEEDS_REVIEW so you know exactly what still
                           needs a human look.

WHY NOT AUTO-FILL EVERYTHING
-----------------------------
A wrong expected_sources entry is worse than an empty one — it would
make eval_retrieval.py report a false "miss" against a document that was
never the real answer, corrupting the one number this whole eval exists
to produce. High-confidence auto-fill + flagged manual review is the
honest middle ground.

USAGE
-----
    python eval/build_eval_set.py                    # just look, don't touch
    python eval/build_eval_set.py --apply             # write high-confidence matches
    python eval/build_eval_set.py --apply --threshold 0.6
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

import config  # noqa: E402


def _tokenise(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _load_corpus():
    """Returns list of dicts: {source, doc_type, department, project}."""
    import chromadb
    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    collection = client.get_collection(config.CHROMA_COLLECTION)

    BATCH = 5000
    total = collection.count()
    all_metas = []
    for offset in range(0, total, BATCH):
        batch = collection.get(include=["metadatas"], limit=BATCH, offset=offset)
        all_metas.extend(batch["metadatas"])

    seen = {}
    for meta in all_metas:
        src = meta.get("source", "")
        if not src or src in seen:
            continue
        seen[src] = {
            "source":     src,
            "doc_type":   meta.get("doc_type", ""),
            "department": meta.get("department", ""),
            "project":    meta.get("project", ""),
        }
    return list(seen.values())


def _score(query_text, category, doc):
    """
    Token-overlap score between a query (+ its category) and a candidate
    document's filename + tagged metadata. Not semantic, deliberately —
    this is meant to be an obvious, auditable heuristic you can trust,
    not another opaque model you have to verify.
    """
    q_tokens = _tokenise(query_text) | _tokenise(category)
    doc_text = " ".join([doc["source"], doc["doc_type"], doc["department"], doc["project"]])
    doc_tokens = _tokenise(doc_text)

    if not q_tokens or not doc_tokens:
        return 0.0

    overlap = q_tokens & doc_tokens
    # Weight distinctive (longer) tokens higher — "aurora" matching matters
    # more than "policy" matching, since "policy" appears in many filenames.
    weighted = sum(len(t) for t in overlap)
    total    = sum(len(t) for t in q_tokens)
    return round(weighted / total, 3) if total else 0.0


def main():
    parser = argparse.ArgumentParser(description="Suggest expected_sources for eval_set.json from your real corpus")
    parser.add_argument("--apply",     action="store_true", help="Write high-confidence matches to eval_set.json")
    parser.add_argument("--threshold", type=float, default=0.5, help="Min score to auto-apply (default 0.5)")
    parser.add_argument("--top",       type=int,   default=5,   help="Candidates to show per query (default 5)")
    args = parser.parse_args()

    eval_path = os.path.join(SCRIPT_DIR, "eval_set.json")
    with open(eval_path) as f:
        data = json.load(f)

    print("Connecting to ChromaDB and loading corpus...")
    try:
        corpus = _load_corpus()
    except Exception as e:
        print(f"✗ Could not connect to ChromaDB at {config.CHROMA_PATH}: {e}")
        print("  Make sure you've indexed documents first: python -m pipeline.indexer")
        sys.exit(1)

    print(f"Found {len(corpus)} unique indexed documents.\n")
    if not corpus:
        print("✗ No documents indexed yet. Nothing to match against.")
        sys.exit(1)

    n_applied, n_review = 0, 0

    for q in data["queries"]:
        if q["expected_sources"]:
            continue  # already filled in — don't touch it

        scored = sorted(
            (( _score(q["query"], q.get("category", ""), doc), doc) for doc in corpus),
            key=lambda x: -x[0],
        )
        top = [(s, d) for s, d in scored[:args.top] if s > 0]

        print(f"[{q['id']}] {q['query']!r}")
        if not top:
            print("    (no candidates found — no indexed doc shares any tokens with this query)")
        for score, doc in top:
            flag = "  <- auto-applied" if args.apply and score >= args.threshold else ""
            print(f"    {score:.2f}  {doc['source']}{flag}")
        print()

        if args.apply:
            best = [d["source"] for s, d in top if s >= args.threshold]
            if best:
                q["expected_sources"] = best
                q["notes"] = (q.get("notes", "") + " [auto-filled, verify before trusting]").strip()
                n_applied += 1
            else:
                q["notes"] = (q.get("notes", "") + " [NEEDS_REVIEW: no confident match found]").strip()
                n_review += 1

    if args.apply:
        with open(eval_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Applied {n_applied} high-confidence matches, flagged {n_review} for manual review.")
        print(f"Wrote: {eval_path}")
        print("\n⚠  These are heuristic token-overlap guesses, not verified ground truth.")
        print("   Open eval_set.json and sanity-check every auto-filled entry before")
        print("   trusting the recall@k numbers this feeds into eval_retrieval.py.")
    else:
        print("Dry run — nothing written. Re-run with --apply to write high-confidence matches.")


if __name__ == "__main__":
    main()
