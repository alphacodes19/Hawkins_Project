# Hawkins Data Archive — Technical README

A fully offline, role-gated Retrieval-Augmented Generation (RAG) system for Hawkins Cookers Limited. Indexes internal PDFs, Word documents, Excel sheets, emails, and SQL exports; answers natural-language queries using a local LLM (qwen2.5:7b via Ollama), grounded in retrieved document context.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Streamlit UI  (app.py + pages/1_Admin.py + auth/ui.py)        │
│  ├── Login gate (auth/ui.py)  — role-based session             │
│  ├── Chat interface           — query → answer + sources       │
│  └── Admin panel              — users, departments, files      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │  Retrieval Pipeline     │
          │  retrieval/retriever.py │
          │                         │
          │  1. Query normalisation │
          │  2. Fuzzy spell-correct │
          │  3. Synonym expansion   │
          │  4. BM25 lexical search │
          │  5. Dense vector search │
          │  6. Metadata fuzzy srch │
          │  7. RRF fusion          │
          │  8. Cross-encoder rerank│
          │  9. Doc grouping (top20)│
          └──────┬──────────────────┘
                 │
    ┌────────────▼──────────────────┐
    │  Answer Generation (app.py)   │
    │  stream_answer()              │
    │  Ollama → qwen2.5:7b (local) │
    │  num_ctx = 16 384 tokens      │
    └───────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Indexing Pipeline  (pipeline/indexer.py)                      │
│  connectors/ → chunker → embedder → ChromaDB                  │
│  BGE-M3 embeddings, 300-word chunks, 50-word overlap          │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Auth / ACL  (auth/db.py, auth/security.py)                    │
│  SQLite · PBKDF2-HMAC-SHA256 · three roles (admin/uploader/   │
│  viewer) · department-scoped doc visibility · hidden_by_admin  │
└────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

```
hawkins_project/
├── app.py                  Main Streamlit app (chat UI, answer generation)
├── config.py               All tunable constants with rationale comments
├── requirements.txt        Python deps + system dep instructions
├── static/
│   └── hawkins_logo_b64.txt  Logo PNG (base64) — shared by app.py & auth/ui.py
│
├── auth/
│   ├── db.py               SQLite ACL: users, roles, departments, file visibility
│   ├── security.py         PBKDF2-HMAC-SHA256 password hashing
│   └── ui.py               Streamlit login gate + session helpers
│
├── pipeline/
│   ├── indexer.py          Orchestrates ingest: connector → chunk → embed → store
│   ├── chunker.py          Word-based chunking with overlap
│   ├── embedder.py         BGE-M3 sentence-transformers wrapper
│   ├── doc_id.py           Content-hash doc identity (stable across re-ingests)
│   ├── metadata_tagger.py  LLM-based doc_type/department/project extraction
│   ├── library.py          Permanent storage for uploaded files
│   ├── utils.py            Shared utilities
│   └── zip_handler.py      ZIP archive extraction
│
├── connectors/
│   ├── pdf_connector.py    PDF text extraction + OCR fallback (pypdfium2/Tesseract)
│   ├── docx_connector.py   Word document extraction
│   ├── excel_connector.py  Excel/CSV extraction
│   ├── email_connector.py  .eml / .msg extraction
│   └── sql_connector.py    SQL dump extraction
│
├── retrieval/
│   ├── retriever.py        Hybrid retrieval pipeline (PRIMARY path)
│   └── generator.py        Standalone generator (DEPRECATED — see file header)
│
├── pages/
│   └── 1_Admin.py          Admin panel: manage users, departments, file ACLs
│
├── scripts/
│   └── migrate_acl.py      One-off migration: backfill doc_id in older ChromaDB entries
│
└── docs/
    ├── README.md           ← this file
    └── HANDOVER_NOTES.md   Deployment status, known issues, next steps
```

---

## Setup

### System dependencies (install before Python packages)

| Dependency | Purpose | Install |
|---|---|---|
| **Ollama** | Local LLM runtime | https://ollama.com/download |
| **Tesseract** | OCR for scanned PDFs | Windows: UB-Mannheim installer; Linux: `apt install tesseract-ocr`; macOS: `brew install tesseract` |

After installing Ollama, pull the model:
```bash
ollama pull qwen2.5:7b
```

### Python dependencies
```bash
pip install -r requirements.txt
```

### Run the app
```bash
streamlit run app.py
```

Default admin credentials on first run: `admin` / `hawkins-change-me`
**Change the password immediately after first login.**

---

## Indexing Documents

Place documents in `data/raw/` (subdirectories: `pdfs/`, `docx/`, `excel/`, `emails/`, `sql/`) or upload them via the Admin panel in the UI.

To re-index from scratch:
```bash
python -m pipeline.indexer --reset
```

To index without resetting (incremental):
```bash
python -m pipeline.indexer
```

> **Note:** `SKIP_TAGGING = True` in `indexer.py` by default for speed. Set to `False` to enable LLM-based metadata tagging (doc_type, department, project). Tagging adds 70–80% to indexing time but improves the metadata-search leg of hybrid retrieval.

---

## Retrieval Pipeline Design

The hybrid pipeline in `retrieval/retriever.py` combines four signals before generating an answer:

1. **BM25 lexical search** — fast keyword matching, word-order independent
2. **Dense vector search** — BGE-M3 semantic embeddings via ChromaDB
3. **Metadata fuzzy search** — matches filename, doc_type, department, project fields
4. **Guaranteed exact-keyword injection** — ensures exact string matches (product codes, invoice numbers) are never dropped by a poor semantic score

These are fused via **Reciprocal Rank Fusion (RRF)** — the correct approach when combining rankers with incompatible score scales (averaging scores would be wrong). Results are then re-ranked by `ms-marco-MiniLM-L-6-v2`, a cross-encoder that scores each (query, chunk) pair independently for relevance.

### Why these choices?

- **BGE-M3** over OpenAI embeddings: fully offline, multilingual (supports Hindi content in Hawkins documents), no API cost or data egress.
- **qwen2.5:7b** over LLaMA: better document grounding (less hallucination), better structured-document understanding (tables, lists), better multilingual support.
- **300-word chunks** (not 512): the ms-marco reranker has a 512-token hard limit. At 512 words ≈ 680 tokens, every chunk was silently truncated. At 300 words ≈ 400 tokens, the full chunk is scored.
- **16 384 token context window**: raised from 8 192 to utilise the larger qwen2.5 context. At 8 docs × 2 chunks × ~300 words ≈ 6 400 tokens, we stay well within the limit.
- **RRF over score averaging**: BM25 scores are unbounded integers; vector distances are 0–2; averaging them is meaningless. RRF uses only rank positions, which are scale-invariant.

---

## ACL Model

Document visibility is determined at retrieval time by `auth/db.py:allowed_doc_ids()`:

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

The default direction is **deny** — new or unmatched documents are invisible until explicitly granted access. This is the safe default for an ACL system.

Security note: This is a Streamlit application-layer ACL, not a cryptographic boundary. Anyone with shell access to the server can read `chroma_db/` directly. Acceptable for an internal tool behind a corporate firewall; document this to whoever signs off on the deployment.

---

## Key Configuration (`config.py`)

| Setting | Value | Rationale |
|---|---|---|
| `CHUNK_SIZE` | 300 words | Reranker 512-token limit; 512 words ≈ 680 tokens caused silent truncation |
| `CHUNK_OVERLAP` | 50 words | Prevents context loss at chunk boundaries |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Multilingual, offline, state-of-the-art |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Better grounding than LLaMA; ~15–25s/answer on CPU |
| `OLLAMA_NUM_CTX` | 16384 | Context window; raised from 8 192 |
| `ANSWER_TOP_DOCS` | 8 | Docs fed to LLM per query |
| `ANSWER_CHUNKS_PER_DOC` | 2 | Chunks per doc; 8×2×~300 words ≈ 6 400 tokens |
| `VECTOR_CANDIDATES` | 200 | Dense recall pool before reranking |
| `BM25_CANDIDATES` | 300 | BM25 recall pool before reranking |

---

## Known Limitations

- **Single-threaded**: Streamlit runs one session per process; concurrent users share a ChromaDB handle.
- **CPU inference**: ~15–25 seconds per answer on the 128GB CPU-only Windows Server. GPU would reduce this to ~2–3s.
- **No query caching**: Identical repeated queries re-embed and re-retrieve on every call.
- **Metadata tagging disabled by default**: `SKIP_TAGGING = True`. Department/project/doc_type filtering runs on empty metadata for documents indexed in fast mode. Set `SKIP_TAGGING = False` and re-index to enable it.
- **No hallucination check**: The prompt instructs the model to stay grounded, but there is no post-generation faithfulness verification against retrieved context.
- **No test suite**: Manual smoke tests only (`test_ocr.py`, `__main__` blocks). A pytest suite is planned — see `HANDOVER_NOTES.md`.

---

## Running Smoke Tests

```bash
# OCR smoke test (requires Tesseract)
python test_ocr.py

# Retrieval smoke test (requires populated ChromaDB + Ollama running)
python -m retrieval.retriever

# Generator smoke test
python -m retrieval.generator
```

---

## Running Tests

```bash
# All unit tests (fast, no Ollama or ChromaDB needed)
pytest tests/

# Exclude integration tests (same as CI)
pytest tests/ -m "not integration"

# With coverage report
pytest tests/ --cov=. --cov-report=term-missing
```

### Test coverage breakdown

| File | What is tested |
|---|---|
| `tests/test_security.py` | PBKDF2 hash format, verify correctness, malformed input, salt uniqueness |
| `tests/test_doc_id.py` | Content hash stability, file vs bytes parity, legacy fallback, `chunk_doc_id()` |
| `tests/test_acl.py` | `init_db()` idempotency, department CRUD, user CRUD, last-admin guard, `register_file()`, all `allowed_doc_ids()` branches |
| `tests/test_retriever_logic.py` | `_normalise()`, `_expand_synonyms()`, `_rrf()` correctness, `_is_allowed()`, `_acl_where()` |

All tests run offline — heavy ML dependencies (ChromaDB, sentence_transformers, Ollama) are mocked in `tests/conftest.py`.

---

## Retrieval Evaluation

The `eval/` directory contains tools to quantify retrieval quality:

### eval/eval_set.json

28 hand-labeled query → expected-document pairs across 7 categories (policy, vendor, finance, product_manual, product_spec, recipe, project). Fill in `expected_sources` with actual filenames from your indexed corpus, then run:

```bash
python eval/eval_retrieval.py --admin --k 5
```

This prints recall@5 for the hybrid pipeline vs. vector-only baseline, per category and overall. The output includes a ready-made resume bullet:

> "Hybrid BM25+dense+rerank pipeline achieved recall@5 of X%, vs Y% for vector-only (+Zpp improvement on 28 labelled queries)"

Options:
```bash
python eval/eval_retrieval.py --admin --k 10           # recall@10
python eval/eval_retrieval.py --admin --category policy # one category only
python eval/eval_retrieval.py --admin --verbose         # per-query breakdown
python eval/eval_retrieval.py --admin --output results.json  # save full results
```

### eval/faithfulness_check.py

Post-generation check that answers are grounded in retrieved context. Three levels:

1. **Citation audit** — every `[source_name]` in the answer must exist in the retrieved chunks
2. **N-gram overlap** — factual sentences (numbers, proper nouns) must have ≥30% trigram overlap with context
3. **LLM self-check** (optional, `--llm`) — asks the LLM to classify each sentence as SUPPORTED / UNSUPPORTED

```bash
# Standalone CLI
python eval/faithfulness_check.py \
  --answer "Employees get 15 days leave [HR_Policy_2024.pdf]." \
  --context "HR_Policy_2024.pdf: Employees are entitled to 15 days of casual leave per year."

# In app.py (call directly)
from eval.faithfulness_check import check_faithfulness
result = check_faithfulness(answer=answer_text, chunks=retrieved_chunks)
if not result["is_faithful"]:
    st.warning(f"Possible hallucination: {result['issues']}")
```

---

## CI / GitHub Actions

`.github/workflows/ci.yml` runs the full unit test suite on every push to `main` or `develop`, on Python 3.11 and 3.12. Integration tests (marked `@pytest.mark.integration`) are excluded from CI and run manually on the server.

```yaml
# What CI runs:
pytest tests/ -m "not integration" -v --tb=short --cov=.
```

To add a new test that requires live infrastructure, decorate it:
```python
@pytest.mark.integration
def test_full_retrieval_pipeline():
    ...
```

---

## Linting

```bash
pip install ruff
ruff check .
```

Config is in `pyproject.toml`. Currently `continue-on-error: true` in CI — lint failures are warnings, not blockers, until the codebase is fully clean.
