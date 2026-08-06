#!/usr/bin/env python3
"""
eval/peek.py — Read actual chunk text for a query, not just filenames
========================================================================
build_eval_set.py can only match on filenames + tagged metadata, which
fails when the right document doesn't happen to share vocabulary with
the query (e.g. "Project Helix budget approval" when the file is named
"02_Project_P003_Project_Helix_Report.pdf" — "budget approval" appears
nowhere in that filename). For those cases, the only honest way to find
ground truth is to look at the actual indexed text.

This script runs real semantic vector search (retrieve(), same function
the app itself falls back to) and prints the top-matching CHUNK TEXT next
to its source filename — so instead of guessing from a filename, you
read a sentence and know immediately whether that's the right document.

USAGE
-----
    python eval/peek.py "Project Helix budget approval"
    python eval/peek.py "minimum whistles for cooking dal" --n 8
    python eval/peek.py --id Q23        # pulls the query text straight from eval_set.json
"""

import argparse
import json
import os
import sys

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)


def _query_text_from_id(qid):
    with open(os.path.join(SCRIPT_DIR, "eval_set.json")) as f:
        data = json.load(f)
    for q in data["queries"]:
        if q["id"] == qid:
            return q["query"]
    print(f"✗ No query with id {qid!r} found in eval_set.json")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Show real chunk text for a query, to confirm ground truth by reading content")
    parser.add_argument("query", nargs="?", help="Query text to search for")
    parser.add_argument("--id", help="Pull query text from eval_set.json by id (e.g. Q23) instead of typing it")
    parser.add_argument("--n", type=int, default=6, help="Number of chunks to show (default 6)")
    args = parser.parse_args()

    if args.id:
        query_text = _query_text_from_id(args.id)
    elif args.query:
        query_text = args.query
    else:
        parser.error("Provide a query string, or --id Q23")

    print(f"Query: {query_text!r}\n")

    from retrieval.retriever import retrieve
    chunks = retrieve(query_text, top_k=args.n, allowed_doc_ids=None)

    if not chunks:
        print("✗ No chunks returned at all — check that documents are indexed "
              "(python -m pipeline.indexer) and ChromaDB has data.")
        return

    for i, c in enumerate(chunks, 1):
        snippet = c["text"].strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        page = f", page {c['page']}" if c.get("page") else ""
        print(f"[{i}] score={c['score']}  {c['source']}{page}")
        print(f"    {snippet}\n")

    print("Read the snippets above — whichever source actually answers the "
          "question is your expected_sources entry. If nothing here answers "
          "it, the document likely doesn't exist in your corpus; leave "
          "expected_sources empty for that query rather than forcing a guess.")


if __name__ == "__main__":
    main()
