"""
backfill_content_sha1.py — one-time backfill of files.content_sha1
===================================================================
Recovers real content SHA-1s for legacy rows (doc_id = "legacy:<filename>")
so exact-duplicate detection can identify renamed byte-identical copies.

RECOVERY ORDER
--------------
For each legacy row that has content_sha1 = NULL, the script tries, in
order, to locate the file's bytes on disk:

  1. Chroma metadata's `filepath` (or `file_path`) — the location the
     original indexer recorded. This is the PRIMARY path: the pre-ACL
     corpus was indexed straight from data/raw/ and data/real_sourced/,
     never through pipeline/library.py.
  2. data/library/ scan by safe-base + extension — the fallback for
     documents that DID go through store_in_library at any point.

If neither locates a readable file, the row is left with
content_sha1 = NULL. Those rows cannot participate in content-based
duplicate detection (an honest, documented limitation).

WHY THE CHROMA CALL NEEDS CARE
------------------------------
An earlier version of this script called

    collection.get(include=["metadatas"])

with no filter and no pagination. Against a large collection Chroma's
SQLite layer builds an `id IN (?, ?, ?, ...)` batch that hits SQLite's
default variable limit (SQLITE_MAX_VARIABLE_NUMBER = 999), producing:

    error returned from database: (code: 1) too many SQL variables

The exception was then swallowed and the script fell through to the
library scan alone — reporting almost everything as "unrecoverable"
because pre-ACL files never lived in data/library/.

This version:
  - Uses `$in` filters batched at 50 sources per query when we know the
    exact source list (the legacy rows), which is well under the SQL
    variable limit AND avoids scanning every chunk.
  - Falls back to paginated `get(limit=PAGE, offset=N)` scans if the
    filtered lookup finds no matches (defensive against unexpected
    metadata field names).
  - Prints a sample of the actual Chroma metadata keys so you can see
    the real field structure rather than assuming.

SAFETY
------
- Idempotent. Running twice is a no-op: rows that already have a
  content_sha1 are skipped.
- `--dry-run` reports every count + focus-file trace WITHOUT writing.
- Never modifies `doc_id`, `source`, ACLs, is_public, hidden_by_admin,
  file_dept, Chroma metadata, or library files.
- Live mode requires interactive confirmation unless `--yes` is passed.

USAGE
-----
    python -m scripts.backfill_content_sha1 --dry-run
    python -m scripts.backfill_content_sha1
    python -m scripts.backfill_content_sha1 --yes
"""

import argparse
import hashlib
import os
import sys
import traceback
from collections import defaultdict

# Allow both `python scripts/backfill_content_sha1.py` and `python -m scripts...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from auth import db as authdb


# Bounded batch sizes.
#
# CHROMA_IN_BATCH: how many source values per `$in` filter. SQLite's default
# variable limit is 999; 50 leaves plenty of headroom and keeps each query
# small and fast. Do not raise this without checking SQLITE_MAX_VARIABLE_NUMBER
# in the deployed Chroma build.
#
# CHROMA_PAGE: page size for the fallback unfiltered scan. Chroma's internal
# batching is stable well below the SQL variable limit at this size.
CHROMA_IN_BATCH = 50
CHROMA_PAGE = 2000

# The file the user specifically wants a full trace for.
FOCUS_SOURCE = "UPSC Wallah Books  Disaster Management.pdf"  # double space intentional


# ── Hashing ─────────────────────────────────────────────────────────────────
def _sha1_16(path: str) -> str:
    """Streaming SHA-1 of the file at `path`, first 16 hex chars.

    Same algorithm as pipeline/doc_id.compute_doc_id() — kept local so this
    script has no dependency on the live indexing pipeline (importing
    pipeline pulls in heavy ML modules).
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


# ── Chroma lookup (paginated + batched) ─────────────────────────────────────
def _open_chroma():
    """Return (collection, sample_meta) or (None, None) on failure."""
    try:
        import chromadb  # lazy so --help works without chroma installed
    except Exception as e:
        print(f"  ERROR: chromadb import failed: {e}")
        return None, None

    try:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        collection = client.get_collection(config.CHROMA_COLLECTION)
    except Exception as e:
        print(f"  ERROR: Chroma open failed: {e}")
        return None, None

    sample = None
    try:
        peek = collection.get(limit=1, include=["metadatas"])
        metas = peek.get("metadatas") or []
        if metas:
            sample = dict(metas[0] or {})
    except Exception as e:
        print(f"  WARNING: metadata sample query failed: {e}")

    return collection, sample


def _get_file_paths_via_filter(collection, needed_sources, stats):
    """Batched `$in` lookup keyed on the `source` field.

    Failures on a single batch are printed and the loop continues — one
    bad batch does not lose the rest.
    """
    source_to_path = {}
    needed = list(needed_sources)
    if not needed:
        return source_to_path

    for start in range(0, len(needed), CHROMA_IN_BATCH):
        batch = needed[start:start + CHROMA_IN_BATCH]
        try:
            result = collection.get(
                where={"source": {"$in": batch}},
                include=["metadatas"],
            )
        except Exception as e:
            print(f"  WARNING: Chroma $in batch [{start}..{start+len(batch)}] failed: {e}")
            stats["chroma_batch_errors"] += 1
            continue

        metas = result.get("metadatas") or []
        stats["chroma_records_inspected"] += len(metas)
        for m in metas:
            if not m:
                continue
            src = m.get("source")
            fp = m.get("filepath") or m.get("file_path")
            if fp:
                stats["chroma_records_with_filepath"] += 1
            if src and fp and src not in source_to_path:
                source_to_path[src] = fp

    return source_to_path


def _get_file_paths_via_scan(collection, stats):
    """Paginated fallback that scans every chunk. Only used if the filtered
    lookup found nothing (defensive against unexpected metadata field
    names). Uses limit+offset which does NOT hit the SQL variable limit.
    """
    source_to_path = {}
    offset = 0
    while True:
        try:
            result = collection.get(
                limit=CHROMA_PAGE, offset=offset, include=["metadatas"],
            )
        except Exception as e:
            print(f"  WARNING: Chroma page at offset {offset} failed: {e}")
            stats["chroma_batch_errors"] += 1
            break

        metas = result.get("metadatas") or []
        if not metas:
            break
        stats["chroma_records_inspected"] += len(metas)
        for m in metas:
            if not m:
                continue
            src = m.get("source")
            fp = m.get("filepath") or m.get("file_path")
            if fp:
                stats["chroma_records_with_filepath"] += 1
            if src and fp and src not in source_to_path:
                source_to_path[src] = fp
        if len(metas) < CHROMA_PAGE:
            break
        offset += CHROMA_PAGE
    return source_to_path


# ── Library-directory index (fallback path resolution) ──────────────────────
def _index_library_dir():
    """Return ({key: [paths...]}, library_dir_str)."""
    try:
        from pipeline.library import LIBRARY_DIR
    except Exception:
        LIBRARY_DIR = os.path.join(config.BASE_DIR, "data", "library")

    if not os.path.isdir(LIBRARY_DIR):
        return {}, LIBRARY_DIR

    by_prefix = defaultdict(list)
    for name in os.listdir(LIBRARY_DIR):
        stem, ext = os.path.splitext(name)
        safe_base = stem.rsplit("__", 1)[0] if "__" in stem else stem
        full = os.path.join(LIBRARY_DIR, name)
        by_prefix[safe_base + ext.lower()].append(full)
        by_prefix[safe_base].append(full)
    return dict(by_prefix), LIBRARY_DIR


def _safe_base_of_source(source: str) -> str:
    """Mirror pipeline/library.store_in_library's basename sanitization."""
    base = os.path.splitext(source)[0]
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in base)


def _resolve_via_library(row, library_index):
    src = row["source"]
    ext = os.path.splitext(src)[1].lower()
    key = _safe_base_of_source(src) + ext
    for cand in library_index.get(key, []):
        if os.path.isfile(cand):
            return cand
    for cand in library_index.get(_safe_base_of_source(src), []):
        if os.path.isfile(cand):
            return cand
    return None


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Backfill files.content_sha1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing anything")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt in live mode")
    args = parser.parse_args()

    hr = "=" * 70
    sub = "-" * 70

    print(hr)
    print("  CONTENT_SHA1 BACKFILL" + ("  —  DRY RUN" if args.dry_run else ""))
    print(hr)
    print(f"  Auth DB     : {authdb.DB_PATH}")
    print(f"  Chroma path : {config.CHROMA_PATH}")
    print(f"  Mode        : {'DRY RUN — nothing will be written' if args.dry_run else 'LIVE'}")
    print()

    # Idempotent column/index create.
    authdb.init_db()

    # ── auth.db snapshot ────────────────────────────────────────────────────
    files = authdb.list_files()
    total = len(files)
    already_set = [f for f in files if f.get("content_sha1")]
    to_process = [f for f in files if not f.get("content_sha1")]
    legacy_rows = [f for f in files if (f.get("doc_id") or "").startswith("legacy:")]

    print(sub)
    print("  auth.db")
    print(sub)
    print(f"  Files rows              : {total}")
    print(f"  Already have SHA-1      : {len(already_set)}")
    print(f"  Need backfill           : {len(to_process)}")
    print(f"  Legacy rows             : {len(legacy_rows)}")
    print()

    if not to_process:
        print("Nothing to backfill. Exiting.")
        return

    # ── Chroma ──────────────────────────────────────────────────────────────
    print(sub)
    print("  Chroma")
    print(sub)
    collection, sample = _open_chroma()

    stats = {
        "chroma_records_inspected": 0,
        "chroma_records_with_filepath": 0,
        "chroma_batch_errors": 0,
    }

    chroma_paths: dict = {}

    if collection is None:
        print("  Chroma unavailable — will rely on data/library/ scan only.")
    else:
        try:
            n_chunks = collection.count()
        except Exception:
            n_chunks = "?"
        print(f"  Collection            : {config.CHROMA_COLLECTION}")
        print(f"  Total chunks          : {n_chunks}")

        if sample:
            keys = sorted(sample.keys())
            print(f"  Sample metadata keys  : {keys}")
            for k in ("source", "filepath", "file_path", "doc_id"):
                if k in sample:
                    v = str(sample[k])
                    if len(v) > 80:
                        v = v[:77] + "..."
                    print(f"    {k:<10} : {v}")
        else:
            print("  Sample metadata       : (none returned)")

        needed_sources = [f["source"] for f in to_process]
        print(f"  Filtered lookup       : {len(needed_sources)} sources, "
              f"batch size {CHROMA_IN_BATCH}")

        chroma_paths = _get_file_paths_via_filter(collection, needed_sources, stats)

        if not chroma_paths:
            print("  Filtered lookup returned 0 paths; falling back to "
                  "paginated full scan.")
            chroma_paths = _get_file_paths_via_scan(collection, stats)

        chroma_paths_exist = 0
        chroma_paths_missing = 0
        for src, fp in chroma_paths.items():
            if os.path.isfile(fp):
                chroma_paths_exist += 1
            else:
                chroma_paths_missing += 1

        print()
        print(f"  Chroma records inspected              : {stats['chroma_records_inspected']}")
        print(f"  Chroma records with file_path         : {stats['chroma_records_with_filepath']}")
        print(f"  Distinct sources with Chroma file_path: {len(chroma_paths)}")
        print(f"  Chroma paths that exist on disk       : {chroma_paths_exist}")
        print(f"  Chroma paths that are missing         : {chroma_paths_missing}")
        if stats["chroma_batch_errors"]:
            print(f"  Chroma batch errors                   : {stats['chroma_batch_errors']}")
    print()

    # ── Library index ───────────────────────────────────────────────────────
    library_index, library_dir = _index_library_dir()
    print(sub)
    print("  data/library/")
    print(sub)
    print(f"  Directory             : {library_dir}")
    print(f"  Distinct index keys   : {len(library_index)}")
    print()

    # ── Recovery pass ───────────────────────────────────────────────────────
    recoverable_chroma = []   # (row, path)
    recoverable_library = []  # (row, path)
    unrecoverable = []
    errors = []

    for row in to_process:
        try:
            src = row["source"]
            fp = chroma_paths.get(src)
            if fp and os.path.isfile(fp):
                recoverable_chroma.append((row, fp))
                continue
            lib_path = _resolve_via_library(row, library_index)
            if lib_path:
                recoverable_library.append((row, lib_path))
                continue
            unrecoverable.append(row)
        except Exception as e:
            errors.append((row, f"{type(e).__name__}: {e}"))

    # ── SHA-1 computation (only for rows we plan to write) ──────────────────
    updates = []  # (doc_id, source, sha1, path, origin)
    for row, path in recoverable_chroma:
        try:
            sha1 = _sha1_16(path)
            updates.append((row["doc_id"], row["source"], sha1, path, "chroma"))
        except Exception as e:
            errors.append((row, f"hash failed ({path}): {type(e).__name__}: {e}"))
    for row, path in recoverable_library:
        try:
            sha1 = _sha1_16(path)
            updates.append((row["doc_id"], row["source"], sha1, path, "library"))
        except Exception as e:
            errors.append((row, f"hash failed ({path}): {type(e).__name__}: {e}"))

    # ── Recovery breakdown ──────────────────────────────────────────────────
    print(sub)
    print("  Recovery breakdown")
    print(sub)
    print(f"  Recoverable through Chroma            : {len(recoverable_chroma)}")
    print(f"  Recoverable through data/library      : {len(recoverable_library)}")
    print(f"  Unrecoverable                         : {len(unrecoverable)}")
    print(f"  Errors                                : {len(errors)}")
    print()

    # ── Content-hash collisions among recovered ─────────────────────────────
    sha_to_rows = defaultdict(list)
    for doc_id, source, sha1, _p, _o in updates:
        sha_to_rows[sha1].append((doc_id, source))
    collisions = {s: rs for s, rs in sha_to_rows.items() if len(rs) > 1}
    if collisions:
        print(sub)
        print("  Content-hash collisions in archive (informational)")
        print(sub)
        print(f"  Distinct SHA-1s appearing in >1 legacy row: {len(collisions)}")
        print(f"  (existing byte-identical files — this script does NOT merge them)")
        shown = 0
        for sha1, rs in collisions.items():
            print(f"    {sha1}: {[s for _, s in rs]}")
            shown += 1
            if shown >= 10:
                print(f"    ... and {len(collisions) - shown} more")
                break
        print()

    # ── Focus-file trace ────────────────────────────────────────────────────
    print(sub)
    print(f"  Focus file trace: {FOCUS_SOURCE!r}")
    print(sub)
    focus_row = next((f for f in files if f["source"] == FOCUS_SOURCE), None)
    if focus_row is None:
        print("  auth.db                     : (not found — no row with that exact source)")
        similar = [f["source"] for f in files
                   if f["source"].strip().lower().startswith("upsc wallah books")][:5]
        if similar:
            print(f"  Similar-looking auth.db sources: {similar}")
    else:
        print(f"  auth.db source              : {focus_row['source']!r}")
        print(f"  auth.db doc_id              : {focus_row['doc_id']}")
        print(f"  auth.db content_sha1        : {focus_row.get('content_sha1')}")
        fp = chroma_paths.get(FOCUS_SOURCE)
        print(f"  Chroma match found          : {'yes' if fp else 'no'}")
        if fp:
            exists = os.path.isfile(fp)
            print(f"  Chroma file_path            : {fp}")
            print(f"  File exists on disk         : {'yes' if exists else 'no'}")
            if exists:
                try:
                    focus_sha = _sha1_16(fp)
                    print(f"  SHA-1 if recovered          : {focus_sha}")
                except Exception as e:
                    print(f"  SHA-1                       : hash failed ({e})")
        lib_path = _resolve_via_library(focus_row, library_index)
        print(f"  data/library/ match         : {lib_path if lib_path else 'no'}")
    print()

    # ── Dry-run vs live ─────────────────────────────────────────────────────
    if args.dry_run:
        print(sub)
        print("  Sample planned updates (first 20)")
        print(sub)
        for doc_id, source, sha1, path, origin in updates[:20]:
            print(f"  [{origin:7}] sha1={sha1}  <-  {source}")
            print(f"              from: {path}")
        if unrecoverable:
            print()
            print(sub)
            print(f"  Unrecoverable (first 10 of {len(unrecoverable)})")
            print(sub)
            for row in unrecoverable[:10]:
                print(f"    {row['doc_id']:<60}  source={row['source']}")
        if errors:
            print()
            print(sub)
            print(f"  Errors (first 10 of {len(errors)})")
            print(sub)
            for row, msg in errors[:10]:
                print(f"    {row.get('doc_id')}  ({row.get('source')}): {msg}")
        print()
        print("Dry run complete. Database not modified.")
        return

    if not args.yes:
        print(f"About to update {len(updates)} rows in {authdb.DB_PATH}.")
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted. Nothing written.")
            return

    # ── Live write ──────────────────────────────────────────────────────────
    backfilled = 0
    for doc_id, source, sha1, _path, _origin in updates:
        try:
            authdb.set_content_sha1(doc_id, sha1)
            backfilled += 1
        except Exception as e:
            errors.append(({"doc_id": doc_id, "source": source}, f"UPDATE failed: {e}"))

    print()
    print(hr)
    print("  DONE")
    print(hr)
    print(f"  already_set                  : {len(already_set)}")
    print(f"  backfilled                   : {backfilled}")
    print(f"  unrecoverable_missing_file   : {len(unrecoverable)}")
    print(f"  errors                       : {len(errors)}")
    print()
    if unrecoverable:
        print("Unrecoverable rows keep content_sha1 = NULL. They cannot")
        print("participate in exact-duplicate detection; a filename-conflict")
        print("check still applies to them via `same_name_conflict`.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
