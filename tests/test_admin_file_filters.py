"""
test_admin_file_filters.py — Feature 9
==========================================
Covers server-side filtering/sorting/limiting on GET /api/admin/files:
filename, uploaded_by, department_id, date range, sort order, limit — and
confirms combinations compose (AND, not OR), and that the old
zero-argument call shape (used by scripts/*, pages/1_Admin.py, and the
rest of the test suite) is completely unaffected.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps
from api.routers import admin as admin_router


@pytest.fixture
def db(tmp_db):
    return tmp_db


def _admin_user():
    return {"id": 1, "username": "admin", "role": "admin", "dept_id": None, "is_active": 1}


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[deps.require_admin] = _admin_user
    return TestClient(app)


def _seed(db):
    depts = db.list_departments()
    sales = next(d for d in depts if d["name"] == "Sales")
    hr = next(d for d in depts if d["name"] == "HR")

    db.register_file(doc_id="h1", source="alpha_report.pdf", uploaded_by="alice",
                     dept_ids=[sales["id"]], is_public=False, content_sha1="h1")
    db.register_file(doc_id="h2", source="beta_report.pdf", uploaded_by="bob",
                     dept_ids=[hr["id"]], is_public=False, content_sha1="h2")
    db.register_file(doc_id="h3", source="gamma_notes.pdf", uploaded_by="alice",
                     dept_ids=[hr["id"]], is_public=True, content_sha1="h3")

    # register_file() all three run within the same wall-clock second, so
    # created_at (second-precision) can tie. Force distinct, deterministic
    # timestamps here so newest/oldest sort tests aren't flaky.
    conn = db.get_conn()
    conn.execute("UPDATE files SET created_at = '2026-01-01T00:00:00+00:00' WHERE doc_id = 'h1'")
    conn.execute("UPDATE files SET created_at = '2026-01-02T00:00:00+00:00' WHERE doc_id = 'h2'")
    conn.execute("UPDATE files SET created_at = '2026-01-03T00:00:00+00:00' WHERE doc_id = 'h3'")
    conn.commit()
    conn.close()

    return sales, hr


# ── Backward compatibility ───────────────────────────────────────────────
def test_zero_arg_list_files_unaffected(db):
    """auth.db.list_files() with no kwargs must keep returning everything,
    ordered by source — the contract every existing caller depends on."""
    _seed(db)
    files = db.list_files()
    assert [f["source"] for f in files] == ["alpha_report.pdf", "beta_report.pdf", "gamma_notes.pdf"]


# ── Filename filter ──────────────────────────────────────────────────────
def test_filename_filter(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"q": "report"})
    sources = {f["source"] for f in r.json()}
    assert sources == {"alpha_report.pdf", "beta_report.pdf"}


def test_filename_filter_case_insensitive(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"q": "ALPHA"})
    sources = {f["source"] for f in r.json()}
    assert sources == {"alpha_report.pdf"}


# ── Uploader filter ──────────────────────────────────────────────────────
def test_uploaded_by_filter(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"uploaded_by": "alice"})
    sources = {f["source"] for f in r.json()}
    assert sources == {"alpha_report.pdf", "gamma_notes.pdf"}


# ── Department filter ─────────────────────────────────────────────────────
def test_department_filter(db, client):
    sales, hr = _seed(db)
    r = client.get("/api/admin/files", params={"department_id": hr["id"]})
    sources = {f["source"] for f in r.json()}
    assert sources == {"beta_report.pdf", "gamma_notes.pdf"}


# ── Combined filters (AND) ────────────────────────────────────────────────
def test_combined_filename_and_uploader_filter(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"q": "report", "uploaded_by": "alice"})
    sources = {f["source"] for f in r.json()}
    assert sources == {"alpha_report.pdf"}


def test_combined_department_and_uploader_filter(db, client):
    sales, hr = _seed(db)
    r = client.get("/api/admin/files", params={"department_id": hr["id"], "uploaded_by": "alice"})
    sources = {f["source"] for f in r.json()}
    assert sources == {"gamma_notes.pdf"}


# ── Sort + limit ──────────────────────────────────────────────────────────
def test_sort_newest_first_is_default(db, client):
    _seed(db)
    r = client.get("/api/admin/files")
    sources = [f["source"] for f in r.json()]
    # h3 (gamma_notes.pdf) was registered last, so newest-first puts it first.
    assert sources[0] == "gamma_notes.pdf"


def test_sort_oldest(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"sort": "oldest"})
    sources = [f["source"] for f in r.json()]
    assert sources[0] == "alpha_report.pdf"


def test_limit(db, client):
    _seed(db)
    r = client.get("/api/admin/files", params={"limit": 2})
    assert len(r.json()) == 2


# ── Date range ────────────────────────────────────────────────────────────
def test_date_range_filter(db):
    """Exercises auth.db.list_files() directly with explicit created_at
    values (registered via three separate calls at different times isn't
    practical within one test process, so this drives the SQL directly)."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO files (doc_id, source, uploaded_by, is_public, created_at, content_sha1) "
        "VALUES ('old', 'old.pdf', 'alice', 1, '2025-01-01T00:00:00+00:00', 'old')"
    )
    conn.execute(
        "INSERT INTO files (doc_id, source, uploaded_by, is_public, created_at, content_sha1) "
        "VALUES ('new', 'new.pdf', 'alice', 1, '2026-06-01T00:00:00+00:00', 'new')"
    )
    conn.commit()
    conn.close()

    recent = db.list_files(date_from="2026-01-01")
    assert {f["source"] for f in recent} == {"new.pdf"}

    early = db.list_files(date_to="2025-12-31")
    assert {f["source"] for f in early} == {"old.pdf"}
