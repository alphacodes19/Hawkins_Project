# legacy/ — Pre-Migration Streamlit Prototype

This is the project's **original working prototype**, before the migration
to the current Next.js + FastAPI system (see `../docs/MIGRATION.md` for
that migration's record). It is kept here deliberately — it's real
evidence of how this project evolved from a working prototype into a
production-shaped system, which is worth showing a reviewer, not hiding.

**This is not part of the running system.** Nothing in `api/`, `auth/`,
`retrieval/`, `connectors/`, `pipeline/`, or `frontend/` imports from or
depends on anything in this folder.

## What's here

- `app.py` — the original Streamlit chat UI and answer-generation logic
- `pages/1_Admin.py` — the original Streamlit admin panel
- `.streamlit/` — Streamlit's own config (theme, page settings)
- `static/hawkins_logo_b64.txt` — a copy of the logo asset `app.py` loads;
  duplicated here (not shared with the live `static/` at the project root)
  specifically so this folder is self-contained and moving/removing it
  later doesn't risk affecting the live system's assets

## If you ever want to run this again

```bash
cd legacy
streamlit run app.py
```

Run it from **inside** `legacy/`, not from the project root — Streamlit
resolves its own `.streamlit/` config relative to your current directory,
not relative to the script's location, so `cd legacy` first matters.

The code itself compensates for having been moved into this subfolder (it
computes the project root correctly regardless of where you invoke it
from, so `import auth`, `import config`, etc. still resolve) — but this
folder was not exhaustively re-tested end-to-end after the move. The one
concrete path dependency found during the move (the logo file) was
identified and fixed directly; there may be others in this ~65KB file that
weren't specifically hunted for, since it's frozen, historical code, not
the maintained system.

## Why it was replaced

Streamlit ties the UI and backend logic into one process — fine for a
prototype, limiting for a multi-user internal tool with real role-based
access control. The current system (`frontend/` + `api/`) separates these
properly: a real REST API the frontend calls, independent of any specific
UI framework, with authorization enforced server-side rather than only in
UI logic.
