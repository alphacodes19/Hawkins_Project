# Hawkins Data Archive — Technical README

A fully offline, role-gated Retrieval-Augmented Generation (RAG) system for
Hawkins Cookers Limited. Indexes internal PDFs, Word documents, Excel
sheets, emails, and SQL exports; answers natural-language queries using a
local LLM (via Ollama), grounded in retrieved document context.

> **Note on this document's history**: an earlier version of this file
> described the project's original Streamlit prototype as the live
> architecture. That prototype still exists (see `legacy/`), but it has
> been superseded by the Next.js + FastAPI system described below, which is
> what actually runs today. This revision corrects that, and also corrects
> a few other claims (test suite, CI, faithfulness checking) that were
> accurate when originally written but are no longer accurate as of the
> current state of the code — noted individually below where relevant.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Next.js frontend (frontend/)                                    │
│  ├── Login (JWT cookie session)                                  │
│  ├── Search — query → results + AI-generated summary             │
│  ├── Profile — photo, password, search history                   │
│  ├── My Uploads — uploader self-service file management          │
│  └── Admin panel — users, departments, files, audit log          │
└───────────────────────────┬────────────────────────────────────────┘
                            │  HTTP + cookie auth
┌───────────────────────────▼────────────────────────────────────────┐
│  FastAPI backend (api/)                                           │
│  Routers: auth, search, upload, files, admin                      │
│  Every request re-derives authorization server-side — the         │
│  frontend hiding a button is never the actual security boundary   │
└───────────────────────────┬────────────────────────────────────────┘
                            │
          ┌─────────────────▼─────────────────┐
          │  Retrieval Pipeline               │
          │  retrieval/retriever.py           │
          │                                    │
          │  1. Query normalisation            │
          │  2. Fuzzy spell-correct            │
          │  3. Synonym expansion              │
          │  4. BM25 lexical search            │
          │  5. Dense vector search (BGE-M3)   │
          │  6. Metadata fuzzy search          │
          │  7. RRF fusion                     │
          │  8. Cross-encoder rerank           │
          │  9. ACL filtering (server-side)    │
          │  10. Doc grouping (top N)          │
          └─────────────┬──────────────────────┘
                        │
    ┌────────────────────▼─────────────────────┐
    │  Answer Generation                       │
    │  Ollama → local LLM, streamed via SSE    │
    │  Post-generation faithfulness check       │
    │  (eval/faithfulness_check.py) flags       │
    │  low-support sentences                    │
    └───────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Indexing Pipeline  (pipeline/indexer.py)                       │
│  connectors/ → chunker → embedder → ChromaDB                   │
│  BGE-M3 embeddings, 300-word chunks, 50-word overlap            │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Auth / ACL / Audit  (auth/db.py, auth/security.py)             │
│  SQLite · PBKDF2-HMAC-SHA256 · three roles (admin/uploader/    │
│  viewer) · department-scoped doc visibility · hidden_by_admin   │
│  · append-only audit_log · per-user search_history               │
└────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

See [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) for the full map,
including what's historical (`legacy/`) and what shouldn't be shared
(`venv311/`, caches, `auth.db.backup`). Summary of the live system:

```
api/          FastAPI routers, schemas, services.py (shared business logic)
auth/         SQLite: users, departments, ACL, audit log, search history
connectors/   pdf_connector.py (+ OCR), docx/excel/email/sql connectors
pipeline/     indexer.py, chunker.py, embedder.py, doc_id.py, library.py, zip_handler.py
retrieval/    retriever.py (hybrid search — PRIMARY path)
frontend/     Next.js app — see frontend/app, frontend/components, frontend/lib
scripts/      migrate_acl.py, backfill_content_sha1.py, cleanup_orphaned_files.py
```

---

## Setup

### System dependencies (install before Python packages)

| Dependency | Purpose | Install |
|---|---|---|
| **Ollama** | Local LLM runtime | https://ollama.com/download |
| **Tesseract** | OCR for scanned PDFs | Windows: UB-Mannheim installer + select needed language packs; Linux: `apt install tesseract-ocr`; macOS: `brew install tesseract` |

Pull the configured model (check `config.py`'s `OLLAMA_MODEL` for the exact
current value — see the note in the configuration table below about this):
```bash
ollama pull qwen2.5:14b
```

### Python dependencies
```bash
pip install -r requirements.txt
pip install -r requirements-api.txt
```

### Run the backend
```bash
uvicorn api.main:app --reload --port 8000
```
Default admin credentials on first run: `admin` / `hawkins-change-me`.
**Change the password immediately after first login.**

### Run the frontend
```bash
cd frontend
npm install
npm run dev
```

---

## Indexing Documents

Place documents in `data/raw/` (subdirectories: `pdfs/`, `docx/`, `excel/`,
`emails/`, `sql/`) or upload them via the web app.

```bash
python -m pipeline.indexer --reset   # full re-index
python -m pipeline.indexer           # incremental
```

> **Still true as of this revision**: `SKIP_TAGGING = True` in
> `pipeline/indexer.py` by default. Set to `False` to enable LLM-based
> metadata tagging (doc_type, department, project) — adds 70–80% to
> indexing time but improves the metadata-search leg of hybrid retrieval.
> This was flagged as a known gap in the original version of this document
> and remains unresolved.

---

## Retrieval Pipeline Design

Unchanged in design from the original prototype — this part of the system
carried over directly into the FastAPI backend rather than being rewritten.
`retrieval/retriever.py` combines four signals before generating an answer:

1. **BM25 lexical search** — fast keyword matching, word-order independent
2. **Dense vector search** — BGE-M3 semantic embeddings via ChromaDB
3. **Metadata fuzzy search** — matches filename, doc_type, department, project fields
4. **Guaranteed exact-keyword injection** — ensures exact string matches (product codes, invoice numbers) are never dropped by a poor semantic score

These are fused via **Reciprocal Rank Fusion (RRF)** — correct when
combining rankers with incompatible score scales (BM25's unbounded integers
vs. vector distances of 0–2; averaging them directly would be meaningless).
Results are then re-ranked by a cross-encoder that scores each
(query, chunk) pair independently for relevance.

### Why these choices?

- **BGE-M3** over an external embedding API: fully offline, multilingual
  (Hindi content support), no API cost or data egress.
- **300-word chunks** (not 512): the reranker has a 512-token hard limit.
  At 512 words ≈ 680 tokens, chunks were being silently truncated. At 300
  words ≈ 400 tokens, the full chunk is scored.
- **RRF over score averaging**: rank-based fusion is scale-invariant;
  averaging BM25 scores with vector distances directly is not meaningful.

---

## ACL Model

Document visibility is determined at retrieval time by
`auth/db.py:allowed_doc_ids()`:

```
hidden_by_admin  →  always invisible (overrides everything)
       ↓
admin role       →  sees all non-hidden documents
       ↓
uploader role    →  own uploads + public docs + dept-matched docs
       ↓
is_public flag   →  visible to all authenticated users
       ↓
department match →  visible to users in the same department
       ↓
deny             →  not returned in retrieval results
```

Default direction is **deny** — new or unmatched documents are invisible
until explicitly granted access.

**Security note** (still accurate): this is an application-layer ACL, not a
cryptographic boundary. Anyone with shell/filesystem access to the server
can read `chroma_db/` or `auth.db` directly. Acceptable for an internal
tool behind a corporate firewall — worth documenting explicitly to whoever
signs off on deployment, same as before.

Every new destructive or ownership-scoped endpoint added since the
original prototype (file deletion, avatar changes, search-history deletion,
bulk file actions) follows the same principle: the backend re-derives
authorization independently on every request, never trusting what the
frontend sent or hid.

---

## Key Configuration (`config.py`)

| Setting | Current value | Note |
|---|---|---|
| `CHUNK_SIZE` | 300 words | Reranker 512-token limit; unchanged from original design |
| `CHUNK_OVERLAP` | 50 words | Prevents context loss at chunk boundaries |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Multilingual, offline |
| `OLLAMA_MODEL` | `qwen2.5:14b` | **See flag below** |
| `OLLAMA_NUM_CTX` | 8192 | **See flag below** |
| `ANSWER_TOP_DOCS` | 5 | **See flag below** |
| `ANSWER_CHUNKS_PER_DOC` | 2 | |
| `VECTOR_CANDIDATES` | 200 | Dense recall pool before reranking |
| `BM25_CANDIDATES` | 300 | BM25 recall pool before reranking |

> **Flag — needs your decision, not something I changed:** `config.py`'s
> own inline comments say the model was "upgraded to qwen2.5:7b", context
> "raised from 8192 → 16384", and top docs "raised from 5 → 8" — but the
> actual live values are `qwen2.5:14b`, `8192`, and `5` respectively. Either
> the values were deliberately changed later (larger model → pulled context
> back down to manage memory, plausibly) and the comments were never
> updated, or the reverse. I don't know which is intentional, so I haven't
> touched either the comments or the values — just flagging the mismatch
> plainly so it doesn't look like an oversight in a review. Worth a two-line
> fix once you confirm which is actually true.

---

## Known Limitations

Status re-checked against the current codebase as of this revision:

- **No query caching** — still true. Identical repeated queries re-embed and re-retrieve every call.
- **Metadata tagging disabled by default** — still true (`SKIP_TAGGING = True`).
- **CPU inference speed** — not re-measured for the current `qwen2.5:14b`
  model. The original ~15–25s/answer figure was measured against
  `qwen2.5:7b`; a 14b model is larger and likely slower on the same
  hardware, but this hasn't been re-benchmarked, so no number is stated
  here rather than repeating a now-unverified one.
- **No longer true — corrected from the original version of this doc:**
  - ~~No test suite~~ → **224 automated tests**, see `tests/`, run with `pytest -m "not integration"`.
  - ~~No CI~~ → `.github/workflows/ci.yml` runs the suite on push/PR to main/develop.
  - ~~No hallucination/faithfulness check~~ → `eval/faithfulness_check.py`
    is wired into the live answer-streaming path; low-support sentences are
    flagged in the API response (the frontend UI element for this was
    intentionally simplified/removed in a later revision — the underlying
    check still runs).

---

## Running Tests

```bash
pytest -m "not integration"          # 224 tests, fast, no live services needed
pytest -m "not integration" --cov=.  # with coverage
cd frontend && npx tsc --noEmit      # frontend type checking
```

All backend tests run offline — ChromaDB, sentence-transformers, and Ollama
are mocked in `tests/conftest.py`.

---

## Retrieval Evaluation

```bash
python eval/eval_retrieval.py --admin --k 5
```
Reports recall@5 for hybrid vs. vector-only retrieval against the labeled
query set in `eval/eval_set.json`. See that file's `expected_sources`
fields — they need real filenames from your indexed corpus filled in to be
meaningful.

```bash
python eval/faithfulness_check.py \
  --answer "Employees get 15 days leave [HR_Policy_2024.pdf]." \
  --context "HR_Policy_2024.pdf: Employees are entitled to 15 days of casual leave per year."
```

---

## CI / Linting

```bash
pytest -m "not integration" -v --tb=short --cov=.   # what CI runs
ruff check .                                          # config in pyproject.toml
```
