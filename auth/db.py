"""
db.py — auth + access-control store
====================================
A small SQLite database, entirely separate from ChromaDB.

Why separate:
  Access rules live here, not in chunk metadata. Chroma metadata values must be
  scalars, so a file's department list can't be stored there without one boolean
  column per department — and changing a permission would then mean re-upserting
  every chunk of that file. Keeping the ACL in SQLite means a permission change
  is a single UPDATE and takes effect on the very next query. No re-indexing.

The link between the two stores is `doc_id`: a content hash written into every
chunk's Chroma metadata (see pipeline/doc_id.py) and used as the primary key of
the `files` table here.

Roles
  admin    — sees everything, manages users / departments / file visibility
  uploader — can upload and set the initial department tags on their own files
  viewer   — read-only

Visibility rules, in order:
  1. hidden_by_admin = 1   → invisible to everyone except admins
  2. requester is admin    → visible
  3. requester uploaded it → visible
  4. is_public = 1         → visible
  5. requester's department is tagged on the file → visible
  6. otherwise             → invisible
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import config
from auth.security import hash_password, verify_password

DB_PATH = os.path.join(config.BASE_DIR, "auth.db")

ROLES = ("admin", "uploader", "viewer")

# Seeded on first run only. The admin is expected to change this immediately
# and to create the real department list from the admin panel.
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "hawkins-change-me"

# Starter departments. These are examples, not a fixed list — the admin panel
# can add, rename, and delete them freely.
SEED_DEPARTMENTS = ["Sales", "HR", "IT", "R&D", "Finance", "Operations"]


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────────────────────
def get_conn():
    """
    A fresh connection per call. Streamlit reruns the script on every
    interaction and may serve concurrent sessions on different threads, so a
    cached module-level connection would eventually raise
    'SQLite objects created in a thread can only be used in that same thread'.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "dept"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    slug    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',
    dept_id       INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,
    uploaded_by     TEXT,
    is_public       INTEGER NOT NULL DEFAULT 0,
    hidden_by_admin INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    content_sha1    TEXT
);

CREATE TABLE IF NOT EXISTS file_dept (
    file_id INTEGER NOT NULL REFERENCES files(id)       ON DELETE CASCADE,
    dept_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (file_id, dept_id)
);

CREATE INDEX IF NOT EXISTS idx_files_doc_id   ON files(doc_id);
CREATE INDEX IF NOT EXISTS idx_file_dept_file ON file_dept(file_id);
-- idx_files_content_sha1 is created by _ensure_content_sha1_column() below,
-- AFTER the ALTER TABLE that adds the column. Creating it here would fail on
-- existing databases whose files table pre-dates the content_sha1 column,
-- because CREATE INDEX ... ON files(content_sha1) is executed by
-- executescript(SCHEMA) before the column exists.

-- One-row-per-key store for one-shot migration flags etc. Kept generic
-- (key/value) rather than one boolean column per migration so future
-- one-time migrations don't each need their own ALTER TABLE.
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per search query (not per session) so an individual query can be
-- deleted without touching the rest of its session. session_id/date_label/
-- start_time are denormalized copies of what the old per-user JSON file
-- stored, kept so the existing session-grouping UI needs no redesign.
CREATE TABLE IF NOT EXISTS search_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    date_label TEXT NOT NULL,
    start_time TEXT NOT NULL,
    query      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_username ON search_history(username);
CREATE INDEX IF NOT EXISTS idx_search_history_session   ON search_history(username, session_id);

-- Append-only. No update_audit_log()/delete_audit_log() function exists on
-- purpose — the admin UI has no path to edit or remove an entry.
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    actor_username TEXT NOT NULL,
    action         TEXT NOT NULL,
    target_type    TEXT NOT NULL,
    target_id      TEXT,
    description    TEXT NOT NULL,
    before_json    TEXT,
    after_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor      ON audit_log(actor_username);
CREATE INDEX IF NOT EXISTS idx_audit_log_action     ON audit_log(action);
"""


# ─────────────────────────────────────────────────────────────────────────────
# One-shot ALTER for databases that pre-date the content_sha1 column.
# CREATE TABLE ... IF NOT EXISTS above is a no-op on an existing table, so it
# will NOT add a new column to a table that was created by an earlier schema.
# This runs additively, is idempotent, and touches nothing else. doc_id is
# never modified.
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_content_sha1_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(files)")}
    if "content_sha1" not in cols:
        conn.execute("ALTER TABLE files ADD COLUMN content_sha1 TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_content_sha1 ON files(content_sha1)")


def _ensure_avatar_column(conn):
    """One-shot ALTER for databases that pre-date profile photos. Same
    additive/idempotent pattern as _ensure_content_sha1_column above."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "avatar_path" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")


def _migrate_search_history_from_json(conn):
    """
    One-time import of the legacy data/processed/search_history/<user>_sessions.json
    files into the search_history table.

    Gated by schema_meta so it runs exactly once per database, ever — not
    "once per user" and not "whenever the JSON file still exists". Gating on
    JSON-file-still-present would be wrong: a user who deletes all their
    migrated history would look "unmigrated" again on the next restart and
    have their deleted entries silently resurrected from the JSON file.
    Gating on a single global flag avoids that entirely.

    Idempotent even if it were somehow re-run: the per-query existence
    check below skips rows already present, so a repeat run cannot create
    duplicates.
    """
    already = conn.execute(
        "SELECT 1 FROM schema_meta WHERE key = 'search_history_migrated_from_json'"
    ).fetchone()
    if already:
        return

    hist_dir = os.path.join(config.BASE_DIR, "data", "processed", "search_history")
    if os.path.isdir(hist_dir):
        usernames = [r["username"] for r in conn.execute("SELECT username FROM users")]
        for username in usernames:
            safe = "".join(c if c.isalnum() else "_" for c in username)
            path = os.path.join(hist_dir, f"{safe}_sessions.json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    sessions = json.load(f)
            except Exception:
                continue  # a corrupt/unreadable file is skipped, not fatal to migration

            # The JSON list is newest-session-first (new sessions are
            # inserted at index 0). Insert oldest-first here so the
            # autoincrement id order reproduces the same recency ordering
            # that list_search_history() below relies on.
            for session in reversed(sessions):
                sid = session.get("session_id")
                date_label = session.get("date_label", "")
                start_time = session.get("start_time", "")
                if not sid:
                    continue
                for query in session.get("queries", []):
                    if not query:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM search_history WHERE username=? AND session_id=? AND query=?",
                        (username, sid, query),
                    ).fetchone()
                    if exists:
                        continue
                    conn.execute(
                        "INSERT INTO search_history "
                        "(username, session_id, date_label, start_time, query, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (username, sid, date_label, start_time, query, _now()),
                    )

    conn.execute(
        "INSERT OR IGNORE INTO schema_meta (key, value) "
        "VALUES ('search_history_migrated_from_json', ?)",
        (_now(),),
    )


def init_db():
    """Create tables and seed the first admin + starter departments. Idempotent."""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _ensure_content_sha1_column(conn)
        _ensure_avatar_column(conn)

        n_depts = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if n_depts == 0:
            conn.executemany(
                "INSERT INTO departments (name, slug) VALUES (?, ?)",
                [(d, _slugify(d)) for d in SEED_DEPARTMENTS],
            )

        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n_users == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, dept_id, created_at) "
                "VALUES (?, ?, 'admin', NULL, ?)",
                (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD), _now()),
            )
        conn.commit()

        # Runs after the block above so a fresh database's seeded admin user
        # already exists (irrelevant to migration itself, but keeps all
        # first-run setup inside one predictable sequence). Separate commit
        # since this does its own multi-statement work.
        _migrate_search_history_from_json(conn)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# DEPARTMENTS
# ─────────────────────────────────────────────────────────────────────────────
def list_departments() -> list:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, slug FROM departments ORDER BY name"
        )]
    finally:
        conn.close()


def add_department(name: str):
    name = name.strip()
    if not name:
        raise ValueError("Department name cannot be empty")
    conn = get_conn()
    try:
        conn.execute("INSERT INTO departments (name, slug) VALUES (?, ?)",
                     (name, _slugify(name)))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Department '{name}' already exists")
    finally:
        conn.close()


def rename_department(dept_id: int, new_name: str):
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Department name cannot be empty")
    conn = get_conn()
    try:
        conn.execute("UPDATE departments SET name = ?, slug = ? WHERE id = ?",
                     (new_name, _slugify(new_name), dept_id))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Department '{new_name}' already exists")
    finally:
        conn.close()


def delete_department(dept_id: int):
    """
    Cascades: file_dept rows for this department vanish, and any user whose
    dept_id pointed here is set to NULL (they keep their account but lose
    department-based access until reassigned).
    """
    conn = get_conn()
    try:
        conn.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────────────────────────
def create_user(username, password, role="viewer", dept_id=None):
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, dept_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), role, dept_id, _now()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"User '{username}' already exists")
    finally:
        conn.close()


def authenticate(username: str, password: str):
    """Return the user dict on success, None on failure."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT u.id, u.username, u.password_hash, u.role, u.dept_id, u.is_active, "
            "       u.avatar_path, d.name AS dept_name "
            "FROM users u LEFT JOIN departments d ON d.id = u.dept_id "
            "WHERE u.username = ?",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not row["is_active"]:
        # Hash anyway so a nonexistent username takes as long as a wrong
        # password — otherwise response time reveals which usernames are real.
        verify_password(password, hash_password("dummy"))
        return None
    if not verify_password(password, row["password_hash"]):
        return None

    user = dict(row)
    user.pop("password_hash")
    return user


def list_users() -> list:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT u.id, u.username, u.role, u.dept_id, u.is_active, u.avatar_path, "
            "       d.name AS dept_name "
            "FROM users u LEFT JOIN departments d ON d.id = u.dept_id "
            "ORDER BY u.username"
        )]
    finally:
        conn.close()


def get_user_by_id(user_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT u.id, u.username, u.role, u.dept_id, u.is_active, u.avatar_path, "
            "       d.name AS dept_name "
            "FROM users u LEFT JOIN departments d ON d.id = u.dept_id "
            "WHERE u.id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_user_avatar(user_id: int, avatar_path):
    """avatar_path is None to clear the photo (fall back to the initial)."""
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path, user_id))
        conn.commit()
    finally:
        conn.close()


def update_user(user_id: int, role=None, dept_id=..., is_active=None):
    """dept_id uses Ellipsis as its sentinel because None is a meaningful value."""
    sets, params = [], []
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        sets.append("role = ?");      params.append(role)
    if dept_id is not ...:
        sets.append("dept_id = ?");   params.append(dept_id)
    if is_active is not None:
        sets.append("is_active = ?"); params.append(int(is_active))
    if not sets:
        return
    params.append(user_id)
    conn = get_conn()
    try:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def set_password(user_id: int, new_password: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (hash_password(new_password), user_id))
        conn.commit()
    finally:
        conn.close()


def delete_file_by_doc_id(doc_id: str):
    """
    Remove a single files row (and, via ON DELETE CASCADE, its file_dept
    rows). Used by scripts/cleanup_orphaned_files.py to remove rows that
    no longer correspond to anything in the current Chroma collection —
    e.g. after a full reindex that dropped removed source files, or a
    duplicate legacy: row left behind once a file was re-migrated under
    its real content hash.

    Does NOT touch Chroma, data/library/, or any other files row. Purely
    a single DELETE against auth.db.files, scoped by doc_id.
    """
    conn = get_conn()
    try:
        conn.execute("DELETE FROM files WHERE doc_id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["role"] == "admin":
            n = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()[0]
            if n <= 1:
                raise ValueError("Cannot delete the last active admin account")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# FILES + ACL
# ─────────────────────────────────────────────────────────────────────────────
def register_file(doc_id, source, uploaded_by=None, dept_ids=None, is_public=False,
                  content_sha1=None):
    """
    Record a file and its department tags. Safe to call on re-upload of the
    same content: doc_id is a content hash for new uploads (or a legacy:
    filename-derived id for pre-ACL corpus rows), so this updates the
    existing row rather than creating a duplicate.

    content_sha1 is the canonical server-computed SHA-1 (first 16 hex chars)
    of the file's bytes when available. It is used exclusively for
    content-based duplicate detection and is NEVER used as a join key —
    doc_id remains the ACL/Chroma join key with its existing semantics.
    Left NULL for legacy rows whose original bytes are unrecoverable.
    """
    conn = get_conn()
    try:
        # COALESCE on the UPDATE branch keeps a previously-set content_sha1
        # if the current call didn't supply one — avoids clobbering a
        # backfilled hash on a subsequent register_file() that doesn't know
        # the hash (e.g. a rare re-registration path).
        conn.execute(
            "INSERT INTO files (doc_id, source, uploaded_by, is_public, created_at, content_sha1) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(doc_id) DO UPDATE SET source = excluded.source, "
            "                                  is_public = excluded.is_public, "
            "                                  content_sha1 = COALESCE(excluded.content_sha1, files.content_sha1)",
            (doc_id, source, uploaded_by, int(is_public), _now(), content_sha1),
        )
        file_id = conn.execute(
            "SELECT id FROM files WHERE doc_id = ?", (doc_id,)
        ).fetchone()["id"]

        conn.execute("DELETE FROM file_dept WHERE file_id = ?", (file_id,))
        for did in (dept_ids or []):
            conn.execute(
                "INSERT OR IGNORE INTO file_dept (file_id, dept_id) VALUES (?, ?)",
                (file_id, did),
            )
        conn.commit()
        return file_id
    finally:
        conn.close()


def set_content_sha1(doc_id: str, content_sha1: str):
    """
    Backfill helper — sets content_sha1 for an existing row without touching
    doc_id, source, ACLs, or anything else. Idempotent: writing the same
    value twice is a no-op.
    """
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE files SET content_sha1 = ? WHERE doc_id = ?",
            (content_sha1, doc_id),
        )
        conn.commit()
    finally:
        conn.close()


def find_files_by_content_sha1(hashes, allowed):
    """
    Return every files row whose content_sha1 is in `hashes`, scoped to
    `allowed` (the caller's allowed_doc_ids result). Uses the indexed
    content_sha1 column; no full-table scan.

      hashes  — iterable of content_sha1 values (16-char hex).
      allowed — either None (admin bypass — no filter) or a set of doc_ids.
                An empty set means "user can see nothing" and this returns [].

    Preserves the same ACL boundary as the existing single-file endpoint:
    a hit here means the user was already allowed to see this document via
    the normal ACL rules; a match on an inaccessible document is filtered
    out and never surfaces in the response.
    """
    hashes = [h for h in (hashes or []) if h]
    if not hashes:
        return []
    if allowed is not None and len(allowed) == 0:
        return []

    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(hashes))
        rows = conn.execute(
            f"SELECT id, doc_id, source, uploaded_by, is_public, hidden_by_admin, "
            f"       created_at, content_sha1 "
            f"FROM files "
            f"WHERE content_sha1 IN ({placeholders}) "
            f"  AND hidden_by_admin = 0",
            tuple(hashes),
        ).fetchall()
        result = [dict(r) for r in rows]
    finally:
        conn.close()

    if allowed is None:
        return result
    return [r for r in result if r["doc_id"] in allowed]


def find_files_by_filenames(filenames, allowed):
    """
    Return every files row whose lower(source) matches any of `filenames`,
    scoped to `allowed`. Filename comparison is case-insensitive to match
    the existing endpoint's behaviour.
    """
    names = [n.lower() for n in (filenames or []) if n]
    if not names:
        return []
    if allowed is not None and len(allowed) == 0:
        return []

    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT id, doc_id, source, uploaded_by, is_public, hidden_by_admin, "
            f"       created_at, content_sha1 "
            f"FROM files "
            f"WHERE lower(source) IN ({placeholders}) "
            f"  AND hidden_by_admin = 0",
            tuple(names),
        ).fetchall()
        result = [dict(r) for r in rows]
    finally:
        conn.close()

    if allowed is None:
        return result
    return [r for r in result if r["doc_id"] in allowed]


def list_files(*, q=None, uploaded_by=None, dept_id=None, date_from=None,
               date_to=None, sort=None, limit=None) -> list:
    """
    Every registered file with its department tags. Used by the admin panel.

    All filter kwargs default to None/unset, and `sort=None` preserves the
    original `ORDER BY source` behaviour — every existing caller
    (scripts/*, pages/1_Admin.py, the test suite) calls this with zero
    arguments and must see identical results to before. The filtering
    below is additive, not a replacement of the old query shape.

      q          — case-insensitive filename substring match
      uploaded_by— exact username match
      dept_id    — only files tagged with this department
      date_from / date_to — 'YYYY-MM-DD', inclusive, matched against the
                   UTC date portion of created_at
      sort       — None (default, by source) | "newest" | "oldest"
      limit      — cap on rows returned, applied server-side so the admin
                   UI never has to pull the entire table to filter client-side
    """
    conn = get_conn()
    try:
        clauses, params = [], []
        if q:
            clauses.append("lower(f.source) LIKE ?"); params.append(f"%{q.lower()}%")
        if uploaded_by:
            clauses.append("f.uploaded_by = ?"); params.append(uploaded_by)
        if date_from:
            clauses.append("date(f.created_at) >= date(?)"); params.append(date_from)
        if date_to:
            clauses.append("date(f.created_at) <= date(?)"); params.append(date_to)

        if sort == "newest":
            order = "f.created_at DESC"
        elif sort == "oldest":
            order = "f.created_at ASC"
        else:
            order = "f.source ASC"

        limit_sql = ""
        if limit:
            limit_sql = "LIMIT ?"

        if dept_id:
            clauses.append("fd.dept_id = ?"); params.append(dept_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            query = (
                "SELECT DISTINCT f.id, f.doc_id, f.source, f.uploaded_by, f.is_public, "
                "       f.hidden_by_admin, f.created_at, f.content_sha1 "
                "FROM files f JOIN file_dept fd ON fd.file_id = f.id "
                f"{where} ORDER BY {order} {limit_sql}"
            )
        else:
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            query = (
                "SELECT f.id, f.doc_id, f.source, f.uploaded_by, f.is_public, "
                "       f.hidden_by_admin, f.created_at, f.content_sha1 "
                f"FROM files f {where} ORDER BY {order} {limit_sql}"
            )

        run_params = list(params) + ([limit] if limit else [])
        files = [dict(r) for r in conn.execute(query, run_params)]
        tags = {}
        for r in conn.execute(
            "SELECT fd.file_id, d.id AS dept_id, d.name "
            "FROM file_dept fd JOIN departments d ON d.id = fd.dept_id"
        ):
            tags.setdefault(r["file_id"], []).append({"id": r["dept_id"], "name": r["name"]})
        for f in files:
            f["departments"] = tags.get(f["id"], [])
        return files
    finally:
        conn.close()


def set_file_departments(doc_id: str, dept_ids: list):
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM files WHERE doc_id = ?", (doc_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown doc_id: {doc_id}")
        conn.execute("DELETE FROM file_dept WHERE file_id = ?", (row["id"],))
        for did in dept_ids:
            conn.execute("INSERT OR IGNORE INTO file_dept (file_id, dept_id) VALUES (?, ?)",
                         (row["id"], did))
        conn.commit()
    finally:
        conn.close()


def set_file_flags(doc_id: str, is_public=None, hidden_by_admin=None):
    sets, params = [], []
    if is_public is not None:
        sets.append("is_public = ?");       params.append(int(is_public))
    if hidden_by_admin is not None:
        sets.append("hidden_by_admin = ?"); params.append(int(hidden_by_admin))
    if not sets:
        return
    params.append(doc_id)
    conn = get_conn()
    try:
        conn.execute(f"UPDATE files SET {', '.join(sets)} WHERE doc_id = ?", params)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# THE ONE FUNCTION THE RETRIEVER CALLS
# ─────────────────────────────────────────────────────────────────────────────
def allowed_doc_ids(user: dict):
    """
    Return the set of doc_ids this user may see.

    Returns None for admins, meaning "no filter — everything". The retriever
    treats None as an explicit bypass. Returning None rather than a set of all
    doc_ids matters: it lets the retriever skip building a Chroma `$in` clause
    with thousands of entries on every admin query.

    An empty set means "this user can see nothing", which is a real, valid
    state (e.g. a viewer with no department assigned and no public files).
    Callers must distinguish `set()` from `None`.
    """
    if not user:
        return set()
    if user.get("role") == "admin":
        return None

    username = user.get("username")
    dept_id  = user.get("dept_id")

    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT f.doc_id
            FROM files f
            LEFT JOIN file_dept fd ON fd.file_id = f.id
            WHERE f.hidden_by_admin = 0
              AND (
                    f.is_public = 1
                 OR f.uploaded_by = ?
                 OR (fd.dept_id IS NOT NULL AND fd.dept_id = ?)
              )
            """,
            (username, dept_id),
        ).fetchall()
        return {r["doc_id"] for r in rows}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH HISTORY
# ─────────────────────────────────────────────────────────────────────────────
def add_search_history(username: str, session_id: str, date_label: str,
                        start_time: str, query: str):
    """
    Records one query. Two behaviours preserved from the old JSON-file
    version (api/services.py's upsert_session_query):
      - a query already logged for this exact session is not duplicated.
      - a brand-new session pushes the user's oldest session out once they
        have more than 50 sessions (previously `sessions[:50]` on write).
    """
    conn = get_conn()
    try:
        dup = conn.execute(
            "SELECT 1 FROM search_history WHERE username=? AND session_id=? AND query=?",
            (username, session_id, query),
        ).fetchone()
        if dup:
            return

        is_new_session = not conn.execute(
            "SELECT 1 FROM search_history WHERE username=? AND session_id=?",
            (username, session_id),
        ).fetchone()

        conn.execute(
            "INSERT INTO search_history "
            "(username, session_id, date_label, start_time, query, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, session_id, date_label, start_time, query, _now()),
        )

        if is_new_session:
            session_rows = conn.execute(
                "SELECT session_id, MIN(id) AS first_id FROM search_history "
                "WHERE username = ? GROUP BY session_id ORDER BY first_id DESC",
                (username,),
            ).fetchall()
            if len(session_rows) > 50:
                stale = [r["session_id"] for r in session_rows[50:]]
                conn.executemany(
                    "DELETE FROM search_history WHERE username = ? AND session_id = ?",
                    [(username, sid) for sid in stale],
                )
        conn.commit()
    finally:
        conn.close()


def list_search_history(username: str) -> list:
    """
    Sessions newest-first (by each session's first-ever query), each with
    its queries oldest-first — same shape/ordering the old JSON version
    produced, except each query now carries its own row `id` so the
    frontend can target a single entry for deletion.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, session_id, date_label, start_time, query "
            "FROM search_history WHERE username = ? ORDER BY id ASC",
            (username,),
        ).fetchall()
    finally:
        conn.close()

    sessions: dict = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "date_label": r["date_label"],
                "start_time": r["start_time"],
                "queries": [],
                "_first_id": r["id"],
            }
        sessions[sid]["queries"].append({"id": r["id"], "query": r["query"]})

    ordered = sorted(sessions.values(), key=lambda s: -s["_first_id"])
    for s in ordered:
        del s["_first_id"]
    return ordered


def delete_search_history_entry(entry_id: int, username: str) -> bool:
    """
    Permanently deletes one search_history row. Ownership is enforced in
    the SQL itself (both id AND username in the WHERE) rather than by a
    separate "is this mine?" check beforehand — a caller cannot delete a
    row it didn't already prove it owns via this single statement.
    Returns True if a row was actually deleted.
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM search_history WHERE id = ? AND username = ?",
            (entry_id, username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────
def record_audit(actor_username: str, action: str, target_type: str,
                  target_id=None, description: str = "", before=None, after=None):
    """
    Append one audit entry. Never raises on a serialization hiccup for
    before/after (falls back to a string) — a broken audit *write* must
    never be the reason a legitimate admin action fails outright, but the
    action itself is still recorded.
    """
    def _safe_json(value):
        if value is None:
            return None
        try:
            return json.dumps(value, default=str)
        except Exception:
            return json.dumps(str(value))

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO audit_log "
            "(created_at, actor_username, action, target_type, target_id, "
            " description, before_json, after_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(), actor_username, action, target_type,
                str(target_id) if target_id is not None else None,
                description, _safe_json(before), _safe_json(after),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit_log(limit: int = 200, actor_username=None, action=None,
                    date_from=None, date_to=None) -> list:
    conn = get_conn()
    try:
        clauses, params = [], []
        if actor_username:
            clauses.append("actor_username = ?"); params.append(actor_username)
        if action:
            clauses.append("action = ?"); params.append(action)
        if date_from:
            clauses.append("date(created_at) >= date(?)"); params.append(date_from)
        if date_to:
            clauses.append("date(created_at) <= date(?)"); params.append(date_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        rows = conn.execute(
            f"SELECT id, created_at, actor_username, action, target_type, target_id, "
            f"       description, before_json, after_json "
            f"FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 2000))),
        ).fetchall()

        out = []
        for r in rows:
            d = dict(r)
            for key in ("before_json", "after_json"):
                raw = d.pop(key)
                out_key = key.replace("_json", "")
                d[out_key] = json.loads(raw) if raw else None
            out.append(d)
        return out
    finally:
        conn.close()


def list_audit_actions() -> list:
    """Distinct action types actually present, for the filter dropdown —
    avoids hardcoding a list in the frontend that could drift from reality."""
    conn = get_conn()
    try:
        return [r["action"] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        )]
    finally:
        conn.close()
