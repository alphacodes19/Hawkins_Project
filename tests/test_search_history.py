"""
test_search_history.py — SQLite-backed search history (Feature 2)
======================================================================
Covers:
  - add_search_history: new session creation, appending to an existing
    session, duplicate-query suppression within a session, 50-session cap
  - list_search_history: session ordering (newest first) and query
    ordering (oldest first) match the old JSON-file behaviour
  - delete_search_history_entry: deletes exactly one row, permanently,
    and only for the owning username
  - the /api/search/history and /api/search/history/{id} endpoints,
    including the cross-user deletion-denied case
  - one-time JSON -> SQLite migration: imports existing data, is
    idempotent, and does not resurrect deleted entries on a second run
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps
from api.routers import search as search_router


def _make_app(current_user: dict):
    app = FastAPI()
    app.include_router(search_router.router)
    app.dependency_overrides[deps.get_current_user] = lambda: current_user
    return app


def _user(username="alice"):
    return {"id": 1, "username": username, "role": "viewer", "dept_id": None, "is_active": 1}


@pytest.fixture
def db(tmp_db):
    return tmp_db


# ── add / list ───────────────────────────────────────────────────────────
def test_new_session_created(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    sessions = db.list_search_history("alice")
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert [q["query"] for q in sessions[0]["queries"]] == ["invoice policy"]


def test_query_appended_to_existing_session(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "leave policy")
    sessions = db.list_search_history("alice")
    assert len(sessions) == 1
    assert [q["query"] for q in sessions[0]["queries"]] == ["invoice policy", "leave policy"]


def test_duplicate_query_in_same_session_not_added_twice(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    sessions = db.list_search_history("alice")
    assert len(sessions[0]["queries"]) == 1


def test_sessions_ordered_newest_first(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "first session query")
    db.add_search_history("alice", "s2", "11 Jan 2026", "09:00 AM", "second session query")
    sessions = db.list_search_history("alice")
    assert [s["session_id"] for s in sessions] == ["s2", "s1"]


def test_50_session_cap_evicts_oldest(db):
    for i in range(55):
        db.add_search_history("alice", f"s{i}", f"day {i}", "09:00 AM", f"query {i}")
    sessions = db.list_search_history("alice")
    assert len(sessions) == 50
    # The 5 oldest sessions (s0..s4) should have been evicted.
    remaining_ids = {s["session_id"] for s in sessions}
    assert "s0" not in remaining_ids
    assert "s54" in remaining_ids


def test_history_isolated_per_user(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "alice query")
    db.add_search_history("bob", "s1", "10 Jan 2026", "09:00 AM", "bob query")
    assert len(db.list_search_history("alice")) == 1
    assert len(db.list_search_history("bob")) == 1
    assert db.list_search_history("alice")[0]["queries"][0]["query"] == "alice query"


# ── delete ───────────────────────────────────────────────────────────────
def test_delete_entry_removes_permanently(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    entry_id = db.list_search_history("alice")[0]["queries"][0]["id"]

    deleted = db.delete_search_history_entry(entry_id, "alice")
    assert deleted is True
    assert db.list_search_history("alice") == []


def test_delete_entry_wrong_owner_denied(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    entry_id = db.list_search_history("alice")[0]["queries"][0]["id"]

    deleted = db.delete_search_history_entry(entry_id, "bob")
    assert deleted is False
    # Alice's entry must still be there — bob's attempt did nothing.
    assert len(db.list_search_history("alice")) == 1


def test_delete_one_of_several_leaves_others(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "query one")
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "query two")
    ids = [q["id"] for q in db.list_search_history("alice")[0]["queries"]]

    db.delete_search_history_entry(ids[0], "alice")
    remaining = db.list_search_history("alice")[0]["queries"]
    assert [q["query"] for q in remaining] == ["query two"]


# ── API endpoints ────────────────────────────────────────────────────────
def test_api_delete_history_entry(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    entry_id = db.list_search_history("alice")[0]["queries"][0]["id"]

    client = TestClient(_make_app(_user("alice")))
    r = client.delete(f"/api/search/history/{entry_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/api/search/history")
    assert r2.json() == []


def test_api_delete_nonexistent_entry_404(db):
    client = TestClient(_make_app(_user("alice")))
    r = client.delete("/api/search/history/999999")
    assert r.status_code == 404


def test_api_cannot_delete_other_users_entry(db):
    db.add_search_history("alice", "s1", "10 Jan 2026", "09:00 AM", "invoice policy")
    entry_id = db.list_search_history("alice")[0]["queries"][0]["id"]

    bob_client = TestClient(_make_app(_user("bob")))
    r = bob_client.delete(f"/api/search/history/{entry_id}")
    assert r.status_code == 404  # not "403 exists but not yours" — no confirmation either way

    # Alice's entry is untouched.
    assert len(db.list_search_history("alice")) == 1


# ── JSON -> SQLite migration ─────────────────────────────────────────────
def test_migration_imports_existing_json_history(tmp_path, monkeypatch):
    import auth.db as authdb
    import config

    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(authdb, "DB_PATH", str(tmp_path / "auth.db"))

    hist_dir = tmp_path / "data" / "processed" / "search_history"
    hist_dir.mkdir(parents=True)
    (hist_dir / "alice_sessions.json").write_text(json.dumps([
        {"session_id": "newer", "date_label": "11 Jan 2026", "start_time": "10:00 AM",
         "queries": ["second session query"]},
        {"session_id": "older", "date_label": "10 Jan 2026", "start_time": "09:00 AM",
         "queries": ["first query", "second query"]},
    ]))

    # init_db() only migrates rows for usernames that already exist, so the
    # user must be created first. create_user() itself calls get_conn(),
    # which is fine before init_db() has run the schema — SQLite creates
    # the file lazily and the users table doesn't exist yet, so call
    # init_db() first to create schema/seed, then add alice, then
    # re-trigger migration by calling init_db() again (idempotent schema
    # creation) is the realistic startup sequence: the seeded admin
    # wouldn't have this file, but a real deployment already has the user
    # created before JSON history could exist for them. Simulate that.
    authdb.init_db()
    authdb.create_user("alice", "pw12345678", role="viewer")
    # Clear the migration flag so init_db()'s second call actually migrates
    # (in production this whole flow only happens once, on the very first
    # startup after the SQLite migration ships).
    conn = authdb.get_conn()
    conn.execute("DELETE FROM schema_meta WHERE key='search_history_migrated_from_json'")
    conn.commit()
    conn.close()

    authdb.init_db()

    sessions = authdb.list_search_history("alice")
    assert [s["session_id"] for s in sessions] == ["newer", "older"]
    assert [q["query"] for q in sessions[1]["queries"]] == ["first query", "second query"]


def test_migration_is_idempotent_and_does_not_resurrect_deletions(tmp_path, monkeypatch):
    import auth.db as authdb
    import config

    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(authdb, "DB_PATH", str(tmp_path / "auth.db"))

    hist_dir = tmp_path / "data" / "processed" / "search_history"
    hist_dir.mkdir(parents=True)
    (hist_dir / "alice_sessions.json").write_text(json.dumps([
        {"session_id": "s1", "date_label": "10 Jan 2026", "start_time": "09:00 AM",
         "queries": ["invoice policy"]},
    ]))

    authdb.init_db()
    authdb.create_user("alice", "pw12345678", role="viewer")
    conn = authdb.get_conn()
    conn.execute("DELETE FROM schema_meta WHERE key='search_history_migrated_from_json'")
    conn.commit()
    conn.close()

    authdb.init_db()  # first real migration
    assert len(authdb.list_search_history("alice")) == 1

    entry_id = authdb.list_search_history("alice")[0]["queries"][0]["id"]
    authdb.delete_search_history_entry(entry_id, "alice")
    assert authdb.list_search_history("alice") == []

    # A second server restart (init_db() called again) must NOT re-import
    # the JSON file and resurrect the entry the user just deleted.
    authdb.init_db()
    assert authdb.list_search_history("alice") == []
