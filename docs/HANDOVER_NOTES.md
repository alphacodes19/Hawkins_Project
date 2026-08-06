# Hawkins Data Archive — Handover Notes

This document is for anyone picking up this project after the initial internship build: a manager reviewing the codebase, a successor developer, or the intern themselves returning after a gap.

---

## What Is Deployed and Working

| Component | Status | Notes |
|---|---|---|
| PDF / DOCX / Excel / Email / SQL ingestion | ✅ Working | All five connector types tested |
| OCR fallback for scanned PDFs | ✅ Working | pypdfium2 → Tesseract per-page |
| BGE-M3 embedding + ChromaDB indexing | ✅ Working | Batch mode (64 chunks/call) |
| BM25 + dense + metadata hybrid retrieval | ✅ Working | RRF fusion, see retriever.py |
| Cross-encoder reranking (ms-marco-MiniLM) | ✅ Working | Chunk size tuned to reranker limit |
| qwen2.5:7b answer generation via Ollama | ✅ Working | Streaming, 16 384 token context |
| Role-based ACL (admin / uploader / viewer) | ✅ Working | Default-deny, department-scoped |
| Admin panel (users, departments, file flags) | ✅ Working | pages/1_Admin.py |
| Query + session history logging | ✅ Working | Per-user JSON in data/history/ |
| ZIP archive ingestion | ✅ Working | pipeline/zip_handler.py |

---

## What Is Known to Be Incomplete or Weak

### High priority (do these before calling it production-ready)

**1. No test suite.**
There are zero pytest tests. The `test_ocr.py` file is a manual smoke test, and most modules have `if __name__ == "__main__"` blocks used as ad-hoc runners. Add tests for at minimum:
- `pipeline/doc_id.py` — content hash stability and collision handling
- `auth/security.py` — password hashing and timing-safe comparison
- `auth/db.py` — ACL visibility logic (the `allowed_doc_ids()` decision tree)
- `retrieval/retriever.py` — the `_rrf()` function and exact-keyword injection

A day's work would cover 15–20 tests. This is the single biggest gap between "intern project" and "engineer's project."

**2. No retrieval evaluation.**
The hybrid retriever is well-designed and well-reasoned, but there are zero numbers proving it outperforms naive vector-only retrieval. Build a small labeled eval set (20–30 query → expected-document pairs from real Hawkins documents) and a script reporting recall@5 for hybrid vs. vector-only. This converts "I built a hybrid retriever" into a defensible, quantified claim.

**3. Default admin password is hardcoded.**
The default `admin` / `hawkins-change-me` credential is in `auth/db.py:init_db()`. There is no forced-rotation-on-first-login mechanic. Document this as a required post-deployment step, or add a `password_changed` flag to the users table and enforce a change on first login.

**4. Metadata tagging is disabled.**
`SKIP_TAGGING = True` in `pipeline/indexer.py`. The metadata-search leg of the hybrid retriever runs on empty `doc_type` / `department` / `project` fields for all documents indexed in fast mode. After the demo, set `SKIP_TAGGING = False` and run a full re-index with `python -m pipeline.indexer --reset`.

### Medium priority

**5. `retrieval/generator.py` is dead code.**
`app.py` generates answers inline via `stream_answer()` and does not call `generator.py`. The file is kept for CLI testing (`__main__` block) and as API documentation. The `num_ctx: 8192` bug has been fixed to use `config.OLLAMA_NUM_CTX`. Do not wire this back into `app.py` without switching it to `retrieve_documents()` (the hybrid path) first.

**6. No GitHub Actions / CI.**
Adding a basic GitHub Actions workflow that runs the future pytest suite on push would take ~30 minutes and signal deployment maturity clearly to any reviewer.

**7. No Docker / containerisation.**
Manual setup instructions are extensive and well-documented in `requirements.txt`, but a `Dockerfile` would make deployment reproducible. The Ollama server needs its own container or a host install — this complicates pure Docker, but a `docker-compose.yml` with the app container + an Ollama sidecar is achievable.

### Low priority / future scope

- **Faithfulness check**: post-generation NLI check that every cited source actually appears in the retrieved context.
- **Query decomposition**: multi-part questions ("leave policy AND 2025 audit") are handled as a single query blob.
- **Token usage logging**: no per-query token count or latency tracking.
- **LRU cache on `embed_text()`**: repeated identical queries re-embed on every call; a simple `functools.lru_cache` would help demo-day performance.

---

## Deployment Environment

- **Server**: Windows Server, 128GB RAM, no GPU, CPU-only inference
- **LLM**: qwen2.5:7b via Ollama (`ollama pull qwen2.5:7b`)
- **Inference speed**: ~15–25 seconds per answer (CPU)
- **ChromaDB**: local SQLite-backed persistent store in `chroma_db/`
- **Auth DB**: SQLite in `auth/hawkins_auth.db`

### Environment variables (Windows Server PowerShell)
```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

---

## Code Paths to Know

| Task | Where to look |
|---|---|
| Change retrieval parameters | `config.py` — all tunables with comments |
| Change answer prompt | `app.py:stream_answer()` — the `ANSWER_PROMPT` string |
| Add a new file format | Create `connectors/myformat_connector.py`, register in `pipeline/indexer.py` |
| Add a new role | `auth/db.py` — update `create_user()` validation and `allowed_doc_ids()` |
| Debug a missing document | Check `auth/db.py:allowed_doc_ids()` — most "why can't I see X" questions trace here |
| Re-index everything | `python -m pipeline.indexer --reset` |
| Add a synonym | `retrieval/retriever.py:SYNONYMS` dict |

---

## Suggested Next Steps (ordered by ROI)

1. Write 15–20 pytest tests (1 day)
2. Build 20-query labeled eval set + recall@5 script (2–3 days)
3. Enable metadata tagging (`SKIP_TAGGING = False`) and re-index
4. Add GitHub Actions CI running the test suite
5. Add a `Dockerfile` + `docker-compose.yml`
6. Add a lightweight faithfulness check on generated answers
7. If frontend upgrade is desired: expose `retrieval/` and `auth/` via FastAPI (they are already Streamlit-decoupled) and build a minimal Next.js frontend on top

---

## What Was Added in Phase 2

### tests/ — pytest suite (122 tests, all passing)

| File | Tests | Covers |
|---|---|---|
| `tests/conftest.py` | — | Shared fixtures, heavy-dep mocking, `tmp_db` fixture |
| `tests/test_security.py` | 27 | PBKDF2 hashing, verify, malformed input, salt uniqueness |
| `tests/test_doc_id.py` | 22 | Content hash correctness, file vs bytes, legacy fallback |
| `tests/test_acl.py` | 39 | Full ACL decision tree, user/dept/file CRUD, last-admin guard |
| `tests/test_retriever_logic.py` | 34 | Normalise, RRF, synonym expansion, `_is_allowed`, `_acl_where` |

Run with: `pytest tests/`

### eval/ — retrieval evaluation and faithfulness checker

- `eval/eval_set.json` — 28 labeled query→document pairs (fill in `expected_sources`)
- `eval/eval_retrieval.py` — recall@k script comparing hybrid vs vector-only
- `eval/faithfulness_check.py` — post-generation hallucination check (citation audit + n-gram overlap + optional LLM self-check)

### .github/workflows/ci.yml

Runs `pytest tests/ -m "not integration"` on push/PR to main/develop, Python 3.11 + 3.12.

### pyproject.toml

pytest and ruff configuration.

---

## Updated Next Steps (what remains)

1. **Fill in eval_set.json** — add real filenames from your indexed corpus to `expected_sources` for each of the 28 queries. Run `python eval/eval_retrieval.py --admin` and record the numbers.
2. **Enable metadata tagging** — set `SKIP_TAGGING = False` in `pipeline/indexer.py` and re-index for better metadata search results.
3. **Hook faithfulness check into app.py** — import `check_faithfulness` and show a `st.warning()` when `is_faithful` is False.
4. **Dockerfile + docker-compose** — low priority but useful for reproducible deployment.
