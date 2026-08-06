"""
migrate_acl.py — one-time backfill for the access-control system
=================================================================
Your corpus was indexed before doc_id existed. Those chunks have no doc_id in
their Chroma metadata, which means:

  * the vector search's `{"doc_id": {"$in": [...]}}` clause will never match
    them (Chroma cannot match a key that is absent), so they'd become invisible
    to every non-admin user
  * they have no row in auth.db, so nothing grants access to them anyway

This script fixes both:

  1. Backfills `doc_id` into every existing chunk's metadata.
     Uses collection.update(), which rewrites metadata WITHOUT re-embedding.
     Re-indexing 340+ documents through BGE-M3 would take hours; this takes
     seconds.

  2. Registers one row per source file in auth.db.

The original bytes of those files are long gone from the indexing code path, so
their content hash can't be recomputed. They get `legacy:<source>` as their
doc_id instead — deterministic, and matches what pipeline.doc_id.chunk_doc_id()
falls back to. Files indexed from here on get a real content hash.

DEFAULT VISIBILITY
------------------
By default every migrated file is registered as PUBLIC. That is deliberate: it
preserves exactly the behaviour the corpus has today (everyone sees everything),
so turning on auth doesn't silently empty the app. Your supervisor then tightens
permissions from the admin panel, file by file.

Run with --private to invert that: everything is registered restricted, visible
to nobody but admins until explicitly tagged. Safer, but the app will look empty
for every non-admin user until someone does the tagging work.

USAGE
    python -m scripts.migrate_acl            # register everything as public
    python -m scripts.migrate_acl --private  # register everything as restricted
    python -m scripts.migrate_acl --dry-run  # show what would change, touch nothing
"""

import argparse
import os
import sys

# Allow `python scripts/migrate_acl.py` as well as `python -m scripts.migrate_acl`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb
import config
from auth import db as authdb
from pipeline.doc_id import legacy_doc_id

BATCH_SIZE = 500   # Chroma update() calls are cheap but not free; batch them


def main():
    parser = argparse.ArgumentParser(description="Backfill doc_id and register files for ACL")
    parser.add_argument("--private", action="store_true",
                        help="Register migrated files as restricted instead of public")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing anything")
    args = parser.parse_args()

    is_public = not args.private

    print("=" * 62)
    print("  ACL MIGRATION — doc_id backfill + file registration")
    print("=" * 62)
    print(f"  Chroma path : {config.CHROMA_PATH}")
    print(f"  Auth DB     : {authdb.DB_PATH}")
    print(f"  Default     : {'PUBLIC (everyone can see)' if is_public else 'RESTRICTED (admins only)'}")
    print(f"  Mode        : {'DRY RUN — nothing will be written' if args.dry_run else 'LIVE'}")
    print()

    if not args.dry_run:
        authdb.init_db()

    client     = chromadb.PersistentClient(path=config.CHROMA_PATH)
    collection = client.get_collection(config.CHROMA_COLLECTION)
    total      = collection.count()
    print(f"Chunks in collection: {total}")

    results = collection.get(include=["metadatas"])
    ids     = results["ids"]
    metas   = results["metadatas"]

    pending_ids, pending_metas = [], []
    sources          = {}   # {source: doc_id}
    already_tagged   = 0

    for cid, meta in zip(ids, metas):
        meta   = dict(meta or {})
        source = meta.get("source", "unknown")

        if meta.get("doc_id"):
            # Already migrated, or indexed after doc_id was introduced.
            already_tagged += 1
            sources.setdefault(source, meta["doc_id"])
            continue

        did = legacy_doc_id(source)
        meta["doc_id"] = did
        sources.setdefault(source, did)

        pending_ids.append(cid)
        pending_metas.append(meta)

    print(f"  already have doc_id : {already_tagged}")
    print(f"  need backfill       : {len(pending_ids)}")
    print(f"  unique source files : {len(sources)}")
    print()

    if args.dry_run:
        print("Files that would be registered:")
        for src, did in sorted(sources.items()):
            print(f"  {did:<40} {src}")
        print("\nDry run complete. Nothing was written.")
        return

    # ── 1. Backfill doc_id into Chroma metadata (no re-embedding) ────────────
    written = 0
    for i in range(0, len(pending_ids), BATCH_SIZE):
        batch_ids   = pending_ids[i:i + BATCH_SIZE]
        batch_metas = pending_metas[i:i + BATCH_SIZE]
        collection.update(ids=batch_ids, metadatas=batch_metas)
        written += len(batch_ids)
        print(f"  backfilled {written}/{len(pending_ids)} chunks", end="\r")
    if pending_ids:
        print()

    # ── 2. Register each source file in auth.db ──────────────────────────────
    registered = 0
    for src, did in sorted(sources.items()):
        authdb.register_file(
            doc_id=did,
            source=src,
            uploaded_by=None,      # provenance unknown for the legacy corpus
            dept_ids=[],           # admin assigns these
            is_public=is_public,
        )
        registered += 1

    # ── 3. Drop the retriever's caches so the new metadata is picked up ──────
    try:
        from retrieval import retriever
        retriever.invalidate_bm25()
    except Exception:
        pass

    print()
    print("=" * 62)
    print(f"  DONE")
    print(f"  Chunks updated  : {written}")
    print(f"  Files registered: {registered}")
    print("=" * 62)
    print()
    print("Next steps:")
    print(f"  1. Log in as '{authdb.DEFAULT_ADMIN_USERNAME}' "
          f"(password: {authdb.DEFAULT_ADMIN_PASSWORD}) and CHANGE THAT PASSWORD.")
    print("  2. Admin panel → Departments: replace the seeded list with the real one.")
    print("  3. Admin panel → Users: create accounts, assign roles + departments.")
    print("  4. Admin panel → Files: tag each file with the departments that may see it.")
    if is_public:
        print()
        print("  NOTE: every migrated file is currently PUBLIC. Until step 4 is done,")
        print("        the ACL is not actually restricting anything.")


if __name__ == "__main__":
    main()
