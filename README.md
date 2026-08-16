# Hawkins Data Archive

An internal, fully offline document search and knowledge-assistant system for
Hawkins Cookers Limited. Employees ask natural-language questions and get
AI-generated, source-cited answers drawn from the company's internal
documents — policies, product specs, financial reports, vendor contracts,
and more — with access scoped by role and department.

> **New here?** Read [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)
> first — it's a two-minute map of what's in this repo and why.

---

## What this actually is, in one paragraph

A Next.js frontend talks to a FastAPI backend. The backend authenticates
users (JWT, cookie-based), enforces role- and department-based access
control (SQLite), and answers queries through a hybrid retrieval pipeline —
BM25 keyword search plus BGE-M3 dense-vector search, fused with Reciprocal
Rank Fusion, then re-ranked by a cross-encoder — before generating a
grounded answer with a locally-hosted LLM (Ollama). Nothing leaves the
building: no external API calls for embeddings or generation.

## Current feature set

- Natural-language search with AI-generated, source-cited summaries
- Hybrid retrieval: BM25 + dense vector search + cross-encoder reranking
- Role-based access control (admin / uploader / viewer) with
  department-scoped document visibility, enforced server-side
- Document ingestion: PDF (with OCR fallback for scanned pages), DOCX,
  Excel, email, SQL dumps, ZIP archives
- Content-hash based duplicate detection on upload
- Per-user profile photos and deletable search history
- Admin file management: hide vs. permanent delete (two distinct,
  intentionally separate operations), multi-file bulk actions, server-side
  filtering (filename / uploader / department / date / sort)
- Append-only admin audit log (who changed what, when, before → after)
- Uploader self-service file management, scoped to files they're allowed
  to see and files they own

## Quick start

### Backend
```bash
pip install -r requirements.txt
pip install -r requirements-api.txt
uvicorn api.main:app --reload --port 8000
```
`init_db()` creates/migrates the SQLite schema automatically on first run —
idempotent, safe to run repeatedly. Default admin on a fresh database:
`admin` / `hawkins-change-me` — **change this immediately.**

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### System dependencies (install before the Python packages above)

| Dependency | Purpose | Install |
|---|---|---|
| **Ollama** | Local LLM runtime | https://ollama.com/download, then `ollama pull qwen2.5:14b` |
| **Tesseract** | OCR for scanned PDFs | Windows: [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) (select the language packs you need, e.g. Hindi); Linux: `apt install tesseract-ocr`; macOS: `brew install tesseract` |

### Tests
```bash
pytest -m "not integration"    # 224 tests, no live Ollama/ChromaDB needed
cd frontend && npx tsc --noEmit
```

## Repository layout (short version)

```
api/ auth/ connectors/ pipeline/ retrieval/ generation/   backend
frontend/                                                  Next.js UI
scripts/  tests/  eval/  static/  data/                   tooling, tests, eval, storage
docs/                                                       full technical docs — start here
legacy/                                                      pre-migration Streamlit prototype (not live)
```

Full explanation of every folder, and what's *not* meant to be shared/committed,
is in [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md).

## Further reading

- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) — what's where, and why
- [`docs/README.md`](docs/README.md) — full technical deep-dive: architecture,
  retrieval pipeline design rationale, ACL model, configuration reference
- [`docs/HANDOVER_NOTES.md`](docs/HANDOVER_NOTES.md) — current status, known
  gaps, suggested next steps
- [`docs/MIGRATION.md`](docs/MIGRATION.md) — the Streamlit → Next.js/FastAPI
  migration record
