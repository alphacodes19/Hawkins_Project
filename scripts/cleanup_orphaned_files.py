"""
cleanup_orphaned_files.py — remove auth.db rows with no live Chroma backing
============================================================================
After a full Chroma reindex (collection reset + rebuilt from source files
on disk), auth.db.files can end up with rows that no longer correspond to
anything in the collection:

  1. GHOST rows — the source file was removed from disk before reindexing
     (e.g. old test data that's now gone). These rows are pure leftovers;
     nothing in Chroma or on disk backs them anymore.

  2. SUPERSEDED rows — a legacy:<filename> row for a file that was
     re-migrated during this same reindex under its real content-hash
     doc_id. Two rows now describe the same logical document: one dead
     (legacy:...), one live (real hash). The dead one is what this script
     removes; the live one is untouched.

This script identifies both categories the same way: for every row in
auth.db.files, check whether its doc_id exists among the doc_ids currently
present in the Chroma collection. If not, it's orphaned.

WHY THIS NEEDS BATCHING
------------------------
Same reasoning as backfill_content_sha1.py and migrate_acl.py: reading
every chunk's metadata from Chroma in one unbounded collection.get() call
risks SQLite's "too many SQL variables" error on a large collection. This
script instead paginates through the collection once and builds a set of
every doc_id actually present — cheap, and bounded regardless of
collection size.

WHAT THIS SCRIPT NEVER TOUCHES
--------------------------------
- Chroma. Not read for writing, not modified at all beyond the read-only
  pagination scan.
- data/library/ or any file on disk.
- Any files row whose doc_id IS present in the current Chroma collection.
- file_dept rows are removed automatically via ON DELETE CASCADE when
  their parent files row is deleted — no separate step needed.

SAFETY
------
- `--dry-run` reports exactly what would be deleted, with the two
  categories broken out, and writes nothing.
- Live mode requires interactive confirmation unless `--yes` is passed.
- Idempotent — rows already cleaned up simply won't appear on a re-run.

USAGE
-----
    python -m scripts.cleanup_orphaned_files --dry-run
    python -m scripts.cleanup_orphaned_files
    python -m scripts.cleanup_orphaned_files --yes
"""

import argparse
import os
import sys
import traceback
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from auth import db as authdb

CHROMA_PAGE = 2000  # same bounded page size used by backfill_content_sha1.py


def _live_doc_ids():
    """
    Return the set of every doc_id currently present in the Chroma
    collection, via a paginated scan. Read-only.
    """
    try:
        import chromadb
    except Exception as e:
        print(f"  ERROR: chromadb import failed: {e}")
        return None

    try:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        collection = client.get_collection(config.CHROMA_COLLECTION)
    except Exception as e:
        print(f"  ERROR: Chroma open failed: {e}")
        return None

    live = set()
    offset = 0
    while True:
        try:
            result = collection.get(limit=CHROMA_PAGE, offset=offset, include=["metadatas"])
        except Exception as e:
            print(f"  ERROR: Chroma page at offset {offset} failed: {e}")
            return None

        metas = result.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            if m and m.get("doc_id"):
                live.add(m["doc_id"])
        if len(metas) < CHROMA_PAGE:
            break
        offset += CHROMA_PAGE

    return live


def main():
    parser = argparse.ArgumentParser(description="Remove orphaned auth.db.files rows")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be removed without writing anything")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt in live mode")
    args = parser.parse_args()

    hr = "=" * 70
    sub = "-" * 70

    print(hr)
    print("  ORPHANED FILES CLEANUP" + ("  —  DRY RUN" if args.dry_run else ""))
    print(hr)
    print(f"  Auth DB     : {authdb.DB_PATH}")
    print(f"  Chroma path : {config.CHROMA_PATH}")
    print(f"  Mode        : {'DRY RUN — nothing will be written' if args.dry_run else 'LIVE'}")
    print()

    live_ids = _live_doc_ids()
    if live_ids is None:
        print("Could not read Chroma. Aborting — refusing to delete anything")
        print("without being able to confirm what's actually live.")
        sys.exit(1)

    files = authdb.list_files()
    total = len(files)

    orphaned = [f for f in files if f["doc_id"] not in live_ids]
    kept = total - len(orphaned)

    # Split orphans into the two categories for a clearer report.
    # SUPERSEDED: a legacy:<source> row where some OTHER row with the same
    # source (case-insensitive) IS live — i.e. that file survived the
    # reindex under a real content hash, this legacy row is the dead twin.
    live_sources_lower = {
        f["source"].lower() for f in files if f["doc_id"] in live_ids
    }
    superseded = []
    ghost = []
    for f in orphaned:
        if f["source"].lower() in live_sources_lower:
            superseded.append(f)
        else:
            ghost.append(f)

    print(sub)
    print("  auth.db")
    print(sub)
    print(f"  Files rows                 : {total}")
    print(f"  Backed by live Chroma data : {kept}")
    print(f"  Orphaned (would remove)    : {len(orphaned)}")
    print(f"    - superseded (dead legacy twin of a live file) : {len(superseded)}")
    print(f"    - ghost (no live file at all, e.g. removed test data) : {len(ghost)}")
    print()

    if not orphaned:
        print("Nothing to clean up. Exiting.")
        return

    if superseded:
        print(sub)
        print(f"  Superseded rows (first 20 of {len(superseded)})")
        print(sub)
        for f in superseded[:20]:
            print(f"    {f['doc_id']:<60}  source={f['source']}")
        print()

    if ghost:
        print(sub)
        print(f"  Ghost rows (first 20 of {len(ghost)})")
        print(sub)
        for f in ghost[:20]:
            print(f"    {f['doc_id']:<60}  source={f['source']}")
        print()

    if args.dry_run:
        print("Dry run complete. Database not modified.")
        return

    if not args.yes:
        print(f"About to delete {len(orphaned)} rows from {authdb.DB_PATH}.")
        print("This only removes auth.db rows — Chroma and files on disk are untouched.")
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted. Nothing deleted.")
            return

    removed = 0
    errors = []
    for f in orphaned:
        try:
            authdb.delete_file_by_doc_id(f["doc_id"])
            removed += 1
        except Exception as e:
            errors.append((f, str(e)))

    print()
    print(hr)
    print("  DONE")
    print(hr)
    print(f"  removed  : {removed}")
    print(f"  errors   : {len(errors)}")
    if errors:
        print()
        print("Errors:")
        for f, msg in errors[:10]:
            print(f"  {f['doc_id']}  ({f['source']}): {msg}")

    remaining = len(authdb.list_files())
    print(f"\n  auth.db.files row count now: {remaining}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
