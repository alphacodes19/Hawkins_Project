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
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_dept (
    file_id INTEGER NOT NULL REFERENCES files(id)       ON DELETE CASCADE,
    dept_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (file_id, dept_id)
);

CREATE INDEX IF NOT EXISTS idx_files_doc_id   ON files(doc_id);
CREATE INDEX IF NOT EXISTS idx_file_dept_file ON file_dept(file_id);
"""


def init_db():
    """Create tables and seed the first admin + starter departments. Idempotent."""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)

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
            "       d.name AS dept_name "
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
            "SELECT u.id, u.username, u.role, u.dept_id, u.is_active, "
            "       d.name AS dept_name "
            "FROM users u LEFT JOIN departments d ON d.id = u.dept_id "
            "ORDER BY u.username"
        )]
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


def delete_user(user_id: int):
    conn = get_conn()
    try:
        # Refuse to delete the last remaining admin — otherwise the panel
        # becomes permanently unreachable.
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
def register_file(doc_id, source, uploaded_by=None, dept_ids=None, is_public=False):
    """
    Record a file and its department tags. Safe to call on re-upload of the
    same content: doc_id is a content hash, so this updates the existing row
    rather than creating a duplicate.
    """
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO files (doc_id, source, uploaded_by, is_public, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(doc_id) DO UPDATE SET source = excluded.source, "
            "                                  is_public = excluded.is_public",
            (doc_id, source, uploaded_by, int(is_public), _now()),
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


def list_files() -> list:
    """Every registered file with its department tags. Used by the admin panel."""
    conn = get_conn()
    try:
        files = [dict(r) for r in conn.execute(
            "SELECT id, doc_id, source, uploaded_by, is_public, hidden_by_admin, created_at "
            "FROM files ORDER BY source"
        )]
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
