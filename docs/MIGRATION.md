# Migrating Hawkins Data Archive from Streamlit to FastAPI + Next.js

This package contains two things:

1. **`api/`** (plus a small addition to `retrieval/retriever.py`) — a FastAPI layer
   to drop straight into your existing project root, alongside `app.py`, `auth/`,
   `retrieval/`, `pipeline/`, `connectors/`, `config.py`. It imports those modules
   exactly like `app.py` did — **nothing in `retrieval/` or `auth/` was rewritten**,
   only `retrieval/retriever.py` gained one additive field (`doc_id` on each
   result), explained below.
2. **`frontend/`** — a standalone Next.js app that talks to that API.

Your existing `app.py` and `pages/1_Admin.py` are untouched and will keep working
if you want to run both side by side during the transition.

---

## 1. Install and merge the backend

Copy into your existing project root:
```
api/                      ← new folder
requirements-api.txt      ← new file
```

Apply the one change to `retrieval/retriever.py`: each result now also carries
a `doc_id` field (see the diff at the bottom of this file, or just take the
whole file from this package — it's a strict superset of what you have).

**Why this one edit was necessary:** the Streamlit UI opened files by a
server-side file path it already trusted. A separate frontend origin can't be
handed a raw filesystem path (that's a path-traversal risk), so file
View/Download in the new UI works by `doc_id` instead, with the ACL check
re-run server-side on every request. `doc_id` wasn't in the result payload
before, so it had to be added. I ran your full test suite before and after —
122/122 still pass; nothing in `tests/` asserts on that dict's exact shape.

```bash
pip install -r requirements.txt
pip install -r requirements-api.txt
```

Run it (from the project root, same requirement `config.py` always had):
```bash
uvicorn api.main:app --reload --port 8000
```

Visit `http://localhost:8000/api/health` — should return `{"status": "ok"}`.
Interactive API docs are auto-generated at `http://localhost:8000/docs`.

### Environment variables (optional, sensible defaults for local dev)

| Variable | Default | Notes |
|---|---|---|
| `HAWKINS_JWT_SECRET` | a dev-only fallback | **Set a real random value in production** — anyone who knows the fallback can forge session tokens. Restarting with a new secret invalidates all sessions. |
| `HAWKINS_COOKIE_SECURE` | `false` | Set to `true` once you're serving over HTTPS, so the session cookie requires it. |
| `HAWKINS_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated list of frontend origins allowed to call the API with credentials. |

---

## 2. Install and run the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local     # edit if your API isn't on localhost:8000
npm run dev
```

Visit `http://localhost:3000`. Default admin login is still `admin` /
`hawkins-change-me` (same seeded account, same warning applies — change it).

I already ran `npm run build` on this exact code during development (verified
production build succeeds, 0 TypeScript errors, all three routes compile) —
you shouldn't hit first-run surprises.

**Whenever you pull an update to `frontend/` that changes `package.json`
(a new dependency was added or a version bumped), re-run `npm install`
before `npm run dev`.** Copying updated source files over an existing
`node_modules` does *not* install anything new — Next.js will fail to
compile with a `Module not found` error for whatever was added, and in dev
mode that failed compile can leave Fast Refresh in a corrupted state where
*other*, unrelated pages start rendering garbled (components overlapping,
duplicated text) until you fix the import and do a full restart. That's not
a second bug — it's a downstream symptom of the first one. If a page ever
looks visually broken like that: check the terminal running `npm run dev`
for a compile error first, fix that, then stop the server, delete `.next/`
(`rm -rf .next`), and restart — don't just refresh the browser.

---

## 3. The cookie/CORS gotcha to know about before deploying

The session cookie is `httpOnly` + `SameSite=Lax`, which is the safe default
and works transparently for local dev (`localhost:3000` → `localhost:8000` —
different ports, same site) and for any deployment where the frontend and API
share a domain (e.g. both behind one reverse-proxy path, `/` → Next.js,
`/api/*` → FastAPI).

**It will *not* work out of the box** if you deploy the frontend and API on
genuinely different domains (e.g. `archive.hawkins.internal` and
`api.hawkins.internal`) — `SameSite=Lax` blocks the cookie on cross-site
`fetch()` calls, including the file View/Download links. Two ways to handle
that when you get there:
- **Recommended:** put both behind one reverse proxy / domain, so it's
  same-site from the browser's point of view (e.g. nginx routing `/api/*` to
  FastAPI and everything else to Next.js). Simplest, no cookie changes needed.
- **Alternative:** switch the cookie to `SameSite=None; Secure` in
  `api/routers/auth.py`, which requires HTTPS everywhere (`HAWKINS_COOKIE_SECURE=true`).

On your current single Windows Server deployment this almost certainly isn't
an issue — just flagging it now so it isn't a surprise later.

---

## 4. What's covered vs. what's a deliberate first-pass simplification

**Fully ported, tested:** login/session, change password, hybrid search,
streamed AI answers (SSE) with the faithfulness warning, file upload +
indexing (all 5 connector types + ZIP), full admin panel (users, departments,
file visibility/ACL), per-user search history logging, secure file
view/download by `doc_id`.

**Simplified from the Streamlit version, worth knowing:**
- The special "render an email as a clean card" formatting in the old
  `render_document_results()` (app.py ~line 495 onward) wasn't ported — emails
  currently show as plain text excerpts like every other file type. Easy
  follow-up if you want it back; I kept scope to what would fit in one pass.
- Session history (`session_queries`) lives in React state, so it resets on a
  full page reload rather than surviving like Streamlit's did across reruns
  within one browser session. The persisted per-user history file
  (`data/processed/search_history/`) still works via `/api/search/history` —
  it's just not wired into the UI as a "resume where I left off" list yet.

Both are additive follow-ups, not architectural gaps — the API already
returns everything needed for either.

---

## 5. Why this should actually fix the lag

Every interaction in the new UI is a targeted API call, not a full script
rerun: clicking to expand a document card is pure client-side state (0
network calls), submitting a search is one `POST /api/search`, and the AI
answer streams token-by-token over SSE instead of blocking behind
`st.write_stream` inside the same rerun that's also re-rendering the sidebar,
the admin nav, and every other result card. The admin panel's tables and
forms are also now only fetched/rendered when you're actually on `/admin`,
instead of `pages/1_Admin.py` being a separate script that Streamlit still
had to manage session state for.
