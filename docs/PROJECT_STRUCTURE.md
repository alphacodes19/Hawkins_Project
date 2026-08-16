# Project Structure

A two-minute map of this repository — what's live, what's historical, and
what shouldn't be here at all. Written for someone reviewing this project
who has never seen it before.

## The system that's actually running today

```
api/          FastAPI backend — routers, schemas, business logic (services.py)
auth/         SQLite-backed users, departments, ACL, audit log, security
connectors/   Per-format text extraction: PDF (+ OCR), DOCX, Excel, email, SQL
pipeline/     Ingestion orchestration: chunking, embedding, ChromaDB indexing
retrieval/    Hybrid search: BM25 + BGE-M3 dense vectors + RRF + cross-encoder
generation/   Synthetic test-corpus generator (see note below — not the LLM
              answer-generation code, which lives inline in retrieval/ + api/)
frontend/     Next.js + TypeScript + Tailwind UI
```

This is the current, primary system. If you're reviewing this project,
this is what to read.

## Supporting directories

```
scripts/      One-off/maintenance scripts (ACL migration, orphaned-file
              cleanup, content-hash backfill) — not run automatically
tests/        pytest suite — 224 tests, run with `pytest -m "not integration"`
eval/         Retrieval-quality evaluation (recall@k) and a post-generation
              faithfulness/hallucination checker
static/       Shared static assets (logo, etc.)
data/         Runtime storage: uploaded files, indexes — gitignored, not
              meant to be committed or shared
docs/         Full technical documentation — start with README.md in here
              for architecture, retrieval design rationale, and the ACL model
```

## About `generation/world_bible.json`, `generation/hawkins_recipes.csv`, `generation/recipe_scrapper.py`

These aren't stray files — they're inputs to `generation/data_generator.py`,
a synthetic test-document generator (confirmed directly in code: it loads
`world_bible.json` for a consistent fictional company/department/people
set, and uses `Faker` to generate realistic PDF/DOCX/Excel/email documents
for testing the pipeline without needing real, sensitive company data).
They now live together in `generation/` (moved from the project root during
this cleanup), with `data_generator.py` and `config.py` both updated to
point at the new location — verified working, not just moved and hoped for.

## `legacy/` — the original prototype

Before the current Next.js/FastAPI system, this project was a working
Streamlit application. It's kept here deliberately, not deleted — it's real
evidence of how the project evolved from a working prototype into a
production-shaped system, which is worth showing a reviewer, not hiding.
See `legacy/README.md` for what's in it and how to run it if you ever need
to. It has **no runtime dependency from anything in `api/`, `auth/`,
`retrieval/`, or `frontend/`** — moving or deleting this folder entirely
would not affect the live system.

## What should never end up in something you share or commit

| Item | Why |
|---|---|
| `venv311/` | Local Python virtual environment — regenerate from `requirements.txt` |
| `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | Pure tool cache, regenerates automatically |
| `frontend/node_modules/`, `frontend/.next/` | Regenerate with `npm install` / `npm run build` |
| `auth.db.backup` | A raw database backup — even with test data, don't hand this out by habit |
| `chroma_db/` (if stale) | Only relevant if it's the vector store the running app actually points at — check `config.py`'s `CHROMA_PATH` before assuming it's live |

All of the above are already listed in `.gitignore`, so a proper `git
clone` of this repo won't include them — the clutter you'd see in Windows
Explorer is your local working copy, not necessarily what's tracked.
**If you're sharing this project as a zip file rather than a git clone**,
these still need excluding by hand — a plain "select all, compress" will
include them. See the `robocopy` example in the accompanying chat for how
to do that cleanly.

## Suggested reorganization — applied in this delivery

```
Hawkins_Project_Sambodh/
├── README.md
├── pyproject.toml  requirements.txt  requirements-api.txt  config.py  .gitignore
├── api/  auth/  connectors/  pipeline/  retrieval/
├── generation/
│   ├── data_generator.py
│   ├── world_bible.json
│   ├── hawkins_recipes.csv
│   └── recipe_scrapper.py
├── frontend/
├── scripts/
│   ├── check_index.py
│   └── test_ocr.py
├── tests/  eval/  static/  data/
├── docs/
│   ├── README.md
│   ├── PROJECT_STRUCTURE.md
│   ├── HANDOVER_NOTES.md
│   ├── MIGRATION.md
│   └── hawkins_project_structure.png
└── legacy/
    ├── README.md
    ├── app.py
    ├── pages/
    ├── static/
    └── .streamlit/
```

This is the actual current layout — not a proposal. Every moved file with
an internal path dependency (`legacy/app.py`'s working-directory setup,
`legacy/pages/1_Admin.py`'s `sys.path` setup, `generation/data_generator.py`'s
reference to `world_bible.json`, `config.py`'s `WORLD_BIBLE_PATH`) was
individually checked and, where the move would have broken it, fixed —
then the full test suite (224 tests) was re-run to confirm nothing in the
live system regressed. `auth.db.backup` was removed entirely rather than
relocated — a raw database backup doesn't belong in anything you hand to a
reviewer, even as "just history."
