"""
test_audit_log.py — Feature 7
=================================
Covers:
  - user create/update(role/department/status/password)/delete all write
    audit entries with correct before/after
  - department create/rename/delete write audit entries
  - the /api/admin/audit-log endpoint and its actor/action/date filters
  - audit_log has no update/delete surface exposed anywhere in the API
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps
from api.routers import admin as admin_router


@pytest.fixture
def db(tmp_db):
    return tmp_db


def _app():
    app = FastAPI()
    app.include_router(admin_router.router)
    return app


def _admin_user(username="admin"):
    return {"id": 1, "username": username, "role": "admin", "dept_id": None, "is_active": 1}


@pytest.fixture
def client(db):
    app = _app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    return TestClient(app)


# ── Users ────────────────────────────────────────────────────────────────
def test_user_created_is_logged(db, client):
    client.post("/api/admin/users", json={
        "username": "rahul", "password": "supersecure1", "role": "viewer", "dept_id": None,
    })
    entries = db.list_audit_log()
    assert any(e["action"] == "USER_CREATED" and e["target_id"] == "rahul" for e in entries)


def test_role_change_is_logged_with_before_after(db, client):
    db.create_user("sambodh", "pw12345678", role="viewer")
    user_id = next(u["id"] for u in db.list_users() if u["username"] == "sambodh")

    client.patch(f"/api/admin/users/{user_id}", json={"role": "uploader"})

    entry = next(e for e in db.list_audit_log() if e["action"] == "ROLE_CHANGED")
    assert entry["before"]["role"] == "viewer"
    assert entry["after"]["role"] == "uploader"
    assert entry["target_id"] == "sambodh"


def test_department_change_is_logged(db, client):
    depts = db.list_departments()
    rnd = next(d for d in depts if d["name"] == "R&D")
    db.create_user("priya", "pw12345678", role="viewer")
    user_id = next(u["id"] for u in db.list_users() if u["username"] == "priya")

    client.patch(f"/api/admin/users/{user_id}", json={"dept_id": rnd["id"]})

    entry = next(e for e in db.list_audit_log() if e["action"] == "DEPARTMENT_CHANGED")
    assert entry["after"]["dept_id"] == rnd["id"]


def test_deactivation_and_reactivation_logged_distinctly(db, client):
    db.create_user("priya", "pw12345678", role="viewer")
    user_id = next(u["id"] for u in db.list_users() if u["username"] == "priya")

    client.patch(f"/api/admin/users/{user_id}", json={"is_active": False})
    client.patch(f"/api/admin/users/{user_id}", json={"is_active": True})

    actions = [e["action"] for e in db.list_audit_log()]
    assert "USER_DEACTIVATED" in actions
    assert "USER_ACTIVATED" in actions


def test_user_update_with_no_field_changes_still_logged_once(db, client):
    db.create_user("priya", "pw12345678", role="viewer")
    user_id = next(u["id"] for u in db.list_users() if u["username"] == "priya")

    r = client.patch(f"/api/admin/users/{user_id}", json={"role": "viewer"})
    assert r.status_code == 200
    entries = [e for e in db.list_audit_log() if e["target_id"] == "priya"]
    assert len(entries) == 1
    assert entries[0]["action"] == "USER_UPDATED"


def test_user_deleted_is_logged_with_username_preserved(db, client):
    db.create_user("temp", "pw12345678", role="viewer")
    user_id = next(u["id"] for u in db.list_users() if u["username"] == "temp")

    client.delete(f"/api/admin/users/{user_id}")

    entry = next(e for e in db.list_audit_log() if e["action"] == "USER_DELETED")
    assert entry["target_id"] == "temp"
    assert entry["before"]["username"] == "temp"


# ── Departments ──────────────────────────────────────────────────────────
def test_department_created_logged(db, client):
    client.post("/api/admin/departments", json={"name": "Legal"})
    entry = next(e for e in db.list_audit_log() if e["action"] == "DEPARTMENT_CREATED")
    assert entry["target_id"] == "Legal"


def test_department_renamed_logged(db, client):
    depts = db.list_departments()
    it = next(d for d in depts if d["name"] == "IT")
    client.patch(f"/api/admin/departments/{it['id']}", json={"name": "IT & Systems"})
    entry = next(e for e in db.list_audit_log() if e["action"] == "DEPARTMENT_RENAMED")
    assert entry["before"]["name"] == "IT"
    assert entry["after"]["name"] == "IT & Systems"


def test_department_deleted_logged(db, client):
    depts = db.list_departments()
    ops = next(d for d in depts if d["name"] == "Operations")
    client.delete(f"/api/admin/departments/{ops['id']}")
    entry = next(e for e in db.list_audit_log() if e["action"] == "DEPARTMENT_DELETED")
    assert entry["before"]["name"] == "Operations"


# ── Audit log API: filters ──────────────────────────────────────────────
def test_audit_log_endpoint_returns_entries_newest_first(db, client):
    client.post("/api/admin/departments", json={"name": "Legal"})
    client.post("/api/admin/departments", json={"name": "Ops2"})

    r = client.get("/api/admin/audit-log")
    entries = r.json()
    assert len(entries) >= 2
    # Newest first — the most recent action (Ops2) should appear before Legal.
    ids = [e["target_id"] for e in entries if e["action"] == "DEPARTMENT_CREATED"]
    assert ids.index("Ops2") < ids.index("Legal")


def test_audit_log_filter_by_actor(db, client):
    app = _app()
    app.dependency_overrides[deps.require_admin] = lambda: _admin_user("girver")
    girver_client = TestClient(app)

    client.post("/api/admin/departments", json={"name": "FromAdmin"})
    girver_client.post("/api/admin/departments", json={"name": "FromGirver"})

    r = client.get("/api/admin/audit-log", params={"actor": "girver"})
    entries = r.json()
    assert all(e["actor_username"] == "girver" for e in entries)
    assert any(e["target_id"] == "FromGirver" for e in entries)


def test_audit_log_filter_by_action(db, client):
    client.post("/api/admin/departments", json={"name": "OnlyDept"})
    client.post("/api/admin/users", json={
        "username": "onlyuser", "password": "supersecure1", "role": "viewer",
    })

    r = client.get("/api/admin/audit-log", params={"action": "USER_CREATED"})
    entries = r.json()
    assert all(e["action"] == "USER_CREATED" for e in entries)
    assert any(e["target_id"] == "onlyuser" for e in entries)


def test_audit_log_date_range_filter(db, client):
    """Same date(created_at) >= date(?) pattern as auth.db.list_files(),
    exercised directly against audit_log rows with explicit timestamps so
    the test isn't relying on wall-clock timing."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO audit_log (created_at, actor_username, action, target_type, target_id, description) "
        "VALUES ('2025-01-01T00:00:00+00:00', 'admin', 'USER_CREATED', 'user', 'old_user', 'old entry')"
    )
    conn.execute(
        "INSERT INTO audit_log (created_at, actor_username, action, target_type, target_id, description) "
        "VALUES ('2026-06-01T00:00:00+00:00', 'admin', 'USER_CREATED', 'user', 'new_user', 'new entry')"
    )
    conn.commit()
    conn.close()

    recent = client.get("/api/admin/audit-log", params={"date_from": "2026-01-01"}).json()
    assert {e["target_id"] for e in recent} == {"new_user"}

    early = client.get("/api/admin/audit-log", params={"date_to": "2025-12-31"}).json()
    assert {e["target_id"] for e in early} == {"old_user"}

    both = client.get(
        "/api/admin/audit-log", params={"date_from": "2025-01-01", "date_to": "2025-12-31"}
    ).json()
    assert {e["target_id"] for e in both} == {"old_user"}


def test_audit_log_actions_endpoint_lists_distinct_actions(db, client):
    client.post("/api/admin/departments", json={"name": "Legal"})
    r = client.get("/api/admin/audit-log/actions")
    assert "DEPARTMENT_CREATED" in r.json()


# ── Audit log is append-only ─────────────────────────────────────────────
def test_no_update_or_delete_route_exists_for_audit_log():
    routes = {(r.path, tuple(sorted(r.methods))) for r in admin_router.router.routes}
    audit_routes = {p for p, m in routes if p.startswith("/api/admin/audit-log")}
    assert audit_routes == {"/api/admin/audit-log", "/api/admin/audit-log/actions"}
    for path, methods in routes:
        if path.startswith("/api/admin/audit-log"):
            assert "DELETE" not in methods
            assert "PATCH" not in methods
            assert "PUT" not in methods
