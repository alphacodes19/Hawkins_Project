# Hawkins Data Archive — Handover Notes

For anyone picking up this project after the internship build: a manager
reviewing the codebase, a successor developer, or the intern themselves
returning after a gap.

> **Revision note:** the previous version of this document described the
> original Streamlit-based prototype and was internally inconsistent (one
> section said "no CI", a later section said CI had been added). This
> revision reflects the actual current state of the Next.js/FastAPI system
> as of the most recent development session, and removes that
> contradiction.

---

## What Is Deployed and Working

| Component | Status | Notes |
|---|---|---|
| Next.js frontend + FastAPI backend | ✅ Working | Superseded the original Streamlit app, kept in `legacy/` |
| PDF / DOCX / Excel / Email / SQL ingestion | ✅ Working | |
| OCR fallback for scanned PDFs | ✅ Working | pypdfium2 → Tesseract per-page; requires the Tesseract engine installed separately from the Python packages (a real gotcha on Windows — see "Environment variables" below) |
| BGE-M3 embedding + ChromaDB indexing | ✅ Working | |
| BM25 + dense + metadata hybrid retrieval | ✅ Working | RRF fusion, see `retrieval/retriever.py` |
| Cross-encoder reranking | ✅ Working | |
| LLM answer generation via Ollama | ✅ Working | Streaming (SSE); current model `qwen2.5:14b` — see the config drift flag in `docs/README.md` |
| Post-generation faithfulness/hallucination check | ✅ Working | `eval/faithfulness_check.py`, wired into the live answer stream |
| Role-based ACL (admin / uploader / viewer) | ✅ Working | Default-deny, department-scoped, enforced server-side |
| Admin panel: users, departments, files, **audit log** | ✅ Working | Audit log is new since the original handover — append-only, no edit/delete route exists anywhere in the API |
| Profile photos | ✅ Working | Pillow-validated (decoded, not trusted by filename/type), re-encoded, server-generated filenames |
| Search history (SQLite-backed, per-entry deletable) | ✅ Working | Migrated from per-user JSON files; migration is idempotent and one-time |
| Uploader self-service file management ("My Uploads") | ✅ Working | ACL-scoped; ownership re-checked server-side on every delete |
| Duplicate detection (content-hash based) | ✅ Working, verified | See flag below re: exact-match-only limitation |
| Automated test suite | ✅ 224 tests passing | `pytest -m "not integration"` |
| CI | ✅ Working | `.github/workflows/ci.yml`, runs on push/PR to main/develop |

---

## What Is Known to Be Incomplete or Weak

### Needs your input specifically

**1. `config.py` comment/value drift.** The inline comments describing
`OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, and `ANSWER_TOP_DOCS` no longer match the
actual values set for those constants. See `docs/README.md`'s
configuration table for the exact mismatch. Nobody currently knows (from
the code alone) whether the comments or the values reflect the actual
intent — needs a two-line fix once that's confirmed.

**2. Duplicate detection is exact-match only, by design — confirm this is
still the accepted tradeoff.** A shortened or edited version of an existing
document (different bytes, same underlying content) will not be flagged.
This came up directly in a real test case during the most recent
development session and was confirmed as expected behavior, not a bug —
documenting it here so it doesn't get rediscovered as a surprise later.

**3. `chroma_db/` vs. wherever `config.py`'s `CHROMA_PATH` actually points.**
If there are multiple ChromaDB directories floating around from earlier
iterations of the project, confirm which one the running app actually
reads before assuming any of them are safe to delete.

### Still true from the original handover (re-verified, not yet fixed)

**4. Metadata tagging is disabled by default.** `SKIP_TAGGING = True` in
`pipeline/indexer.py`. Department/project/doc_type filtering runs on empty
metadata for documents indexed in fast mode.

**5. No query caching.** Identical repeated queries re-embed and re-retrieve
on every call.

**6. No Docker/containerization.** Manual setup instructions exist and are
current (see root `README.md`), but there's still no `Dockerfile` /
`docker-compose.yml`.

### Resolved since the original handover

- ~~No test suite~~ → 224 tests, `tests/`
- ~~No retrieval evaluation~~ → `eval/eval_retrieval.py` + `eval/eval_set.json`
- ~~No CI~~ → `.github/workflows/ci.yml`
- ~~No hallucination check~~ → `eval/faithfulness_check.py`, live in the answer stream
- ~~Default admin password hardcoded with no rotation mechanic~~ → still
  hardcoded as the *first-run seed* (this is normal/expected for a fresh
  database), but a proper `change-password` endpoint now exists and is
  exposed in the UI (Profile page) — using it is a manual step, not
  enforced automatically on first login. Enforcing that remains open, if wanted.

---

## Deployment Environment

- **Server**: Windows Server, 128GB RAM, no GPU, CPU-only inference (as of
  the original deployment — re-confirm this hasn't changed)
- **LLM**: current configured model is `qwen2.5:14b` via Ollama — see the
  config drift flag above before assuming this is the intended final value
- **ChromaDB**: local persistent store — confirm the actual path via
  `config.py`'s `CHROMA_PATH`
- **Auth DB**: SQLite, `auth.db`

### Environment variables (Windows PowerShell)

```powershell
[System.Environment]::SetEnvironmentVariable("TESSERACT_CMD", "C:\Program Files\Tesseract-OCR\tesseract.exe", "User")
```

**Two gotchas confirmed the hard way during a real debugging session, worth
recording here so the next person doesn't lose the same hour:**

1. Setting this env var only takes effect in **terminal windows opened
   after** the change — an already-open terminal (including one running the
   backend) will not see it. Close and reopen fully.
2. The backend caches its OCR-availability check once per process
   (`_probe_ocr()` in `connectors/pdf_connector.py`). Installing Tesseract
   or fixing this env var **after** the backend has already started will
   not take effect until the backend process is actually restarted — a
   `--reload` file-change restart does not re-read environment variables,
   only a full process restart does.
3. If Hindi-language OCR is needed (the app is configured for
   `eng+hin`), the Hindi trained-data file
   (`tessdata/hin.traineddata`) is a separate download from the base
   Tesseract installer and does not come bundled by default — confirmed
   missing on at least one real deployment.

---

## Code Paths to Know

| Task | Where to look |
|---|---|
| Change retrieval parameters | `config.py` |
| Change the answer prompt | search for the prompt construction in `retrieval/` / `api/services.py` |
| Add a new file format | `connectors/myformat_connector.py`, register in `pipeline/indexer.py` |
| Add a new role | `auth/db.py` — `create_user()` validation and `allowed_doc_ids()` |
| Debug a missing document | `auth/db.py:allowed_doc_ids()` — most "why can't I see X" questions trace here |
| Debug OCR not firing | `connectors/pdf_connector.py:_probe_ocr()` — and remember the process-restart gotcha above |
| Re-index everything | `python -m pipeline.indexer --reset` |
| Trace an admin action | Admin panel → Audit Log tab, or `auth/db.py:list_audit_log()` |

---

## Suggested Next Steps (ordered by likely value)

1. Resolve the `config.py` comment/value drift (5 minutes, but needs a real decision).
2. Confirm which `chroma_db/` directory (if more than one exists) is actually live.
3. Decide whether "revised version of an existing document" duplicate
   detection is wanted as a real feature — it would need a different
   technique (text/embedding similarity) than the current exact-hash
   approach, and hasn't been designed yet.
4. Re-benchmark answer latency against the current `qwen2.5:14b` model —
   the ~15–25s figure on record is for the older `qwen2.5:7b`.
5. Enable metadata tagging (`SKIP_TAGGING = False`) and re-index, if the
   metadata-search leg of retrieval is underperforming.
6. Add a `Dockerfile` + `docker-compose.yml` for reproducible deployment.
7. Enforce password rotation on first login for newly created accounts.
