# Hawkins — Duplicate Detection Correction

Drop-in files for the duplicate-detection correctness fix.
Baseline: 122 tests pass. After changes: 139 tests pass (17 new).

## File layout (mirrors the project)

    auth/db.py                          — MODIFIED
    api/routers/upload.py               — MODIFIED
    api/services.py                     — MODIFIED
    scripts/backfill_content_sha1.py    — NEW
    tests/test_duplicate_detection.py   — NEW
    tests/test_backfill_content_sha1.py — NEW
    frontend/lib/api.ts                 — MODIFIED
    frontend/components/UploadDialog.tsx — MODIFIED

Copy each file to the same path in the Hawkins_Project-main repo, overwriting
the existing MODIFIED files. The two NEW test files and the new script have no
existing counterpart.

## What changed

- New nullable column `files.content_sha1` + index `idx_files_content_sha1`
  (additive; `doc_id` unchanged).
- Duplicate detection uses `content_sha1` — not `doc_id` — so a renamed
  byte-identical copy of a legacy document is now correctly identified as
  `exact_duplicate` once its hash has been backfilled.
- New endpoint `POST /api/upload/check-batch` — one indexed round-trip
  instead of N per-file scans. `POST /api/upload/check` still works.
- Frontend dialog uses the batch endpoint. Every file still gets hashed
  (correctness requirement) — no name-first shortcut.
- Server keeps recomputing SHA-1 on the tempfile; client hash is only used
  for the pre-check, never trusted as canonical.

## After applying — order of operations

1. Back up `auth.db`.
2. Restart the API. `init_db()` adds the column + index on startup
   (idempotent; safe if already present).
3. Dry-run the backfill:
       python -m scripts.backfill_content_sha1 --dry-run
   Review the reported counts and the "would set SHA-1" sample.
4. Live backfill:
       python -m scripts.backfill_content_sha1
   (or `--yes` to skip the confirmation prompt).
5. Run the tests:
       pytest -m "not integration"

## Notes

- Legacy rows whose original bytes are gone from disk stay with
  content_sha1 = NULL. They cannot participate in content-based
  duplicate detection — filename-conflict remains the only signal for
  those. Test `test_case6` documents this limitation.
- `doc_id` is never modified anywhere in these changes. Chroma metadata
  is never touched. `pipeline/`, `scripts/migrate_acl.py`, the search
  pipeline, the viewer, admin panel, and ACL rules are untouched.
