"""
test_file_management.py — Features 4, 5, 7, 8
==================================================
Covers:
  - admin permanent delete: removes Chroma chunks + auth.db row + physical
    file, and is distinct from hide (which touches none of Chroma/fs)
  - admin hide/unhide: only auth.db's hidden_by_admin flag changes
  - bulk actions: independent per-item authorization/validation, partial
    failure doesn't abort the batch
  - uploader can delete their own file; cannot delete another uploader's
    file; viewer cannot delete at all
  - every mutating action above writes a matching audit_log row
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps, services
from api.routers import admin as admin_router
from api.routers import files as files_router


class _FakeCollection:
    """Minimal Chroma-collection stand-in supporting only what
    delete_file_completely() calls: get(where=...) and delete(where=...)."""

    def __init__(self, doc_id_to_path):
        self._doc_id_to_path = dict(doc_id_to_path)

    def get(self, where=None, limit=None, include=None):
        doc_id = (where or {}).get("doc_id")
        path = self._doc_id_to_path.get(doc_id)
        if path is None:
            return {"metadatas": []}
        return {"metadatas": [{"file_path": path, "doc_id": doc_id}]}

    def delete(self, where=None):
        doc_id = (where or {}).get("doc_id")
        self._doc_id_to_path.pop(doc_id, None)


@pytest.fixture
def db(tmp_db):
    return tmp_db


@pytest.fixture
def library_env(tmp_path, monkeypatch):
    lib_dir = tmp_path / "library"
    lib_dir.mkdir()
    monkeypatch.setattr(services, "LIBRARY_DIR", str(lib_dir))
    return str(lib_dir)


def _make_file(db, library_env, doc_id, source, uploaded_by=None, is_public=True, dept_ids=None):
    path = os.path.join(library_env, f"{doc_id}.pdf")
    with open(path, "wb") as f:
        f.write(b"file bytes for " + doc_id.encode())
    db.register_file(doc_id=doc_id, source=source, uploaded_by=uploaded_by,
                     dept_ids=dept_ids or [], is_public=is_public, content_sha1=doc_id)
    return path


def _install_fake_collection(monkeypatch, mapping):
    fake = _FakeCollection(mapping)
    monkeypatch.setattr(services, "get_chroma_collection", lambda: fake)
    monkeypatch.setattr(services, "invalidate_bm25", lambda: None)
    return fake


def _admin_app():
    app = FastAPI()
    app.include_router(admin_router.router)
    return app


def _files_app():
    app = FastAPI()
    app.include_router(files_router.router)
    return app


def _admin_user():
    return {"id": 1, "username": "admin", "role": "admin", "dept_id": None, "is_active": 1}


def _uploader_user(username, dept_id=None):
    return {"id": 2, "username": username, "role": "uploader", "dept_id": dept_id, "is_active": 1}


def _viewer_user(username="vic"):
    return {"id": 3, "username": username, "role": "viewer", "dept_id": None, "is_active": 1}


# ── Admin permanent delete ──────────────────────────────────────────────────
def test_admin_permanent_delete_removes_all_three_layers(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashA", "report.pdf", uploaded_by="alice")
    fake = _install_fake_collection(monkeypatch, {"hashA": path})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    r = client.delete("/api/admin/files/hashA")
    assert r.status_code == 200
    assert r.json()["warnings"] == []

    assert not any(f["doc_id"] == "hashA" for f in db.list_files())   # auth.db
    assert "hashA" not in fake._doc_id_to_path                        # chroma
    assert not os.path.exists(path)                                   # filesystem


def test_admin_permanent_delete_unknown_doc_id_404(db, library_env, monkeypatch):
    _install_fake_collection(monkeypatch, {})
    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    r = client.delete("/api/admin/files/does-not-exist")
    assert r.status_code == 404


def test_admin_permanent_delete_writes_audit_log(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashA", "report.pdf", uploaded_by="alice")
    _install_fake_collection(monkeypatch, {"hashA": path})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)
    client.delete("/api/admin/files/hashA")

    entries = db.list_audit_log()
    assert any(e["action"] == "FILE_DELETED" and e["target_id"] == "hashA" for e in entries)
    assert entries[0]["actor_username"] == "admin"


# ── Admin hide / unhide ──────────────────────────────────────────────────────
def test_hide_does_not_touch_chroma_or_filesystem(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashA", "report.pdf")
    fake = _install_fake_collection(monkeypatch, {"hashA": path})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    r = client.patch("/api/admin/files/hashA/flags", json={"hidden_by_admin": True})
    assert r.status_code == 200

    row = next(f for f in db.list_files() if f["doc_id"] == "hashA")
    assert row["hidden_by_admin"] == 1
    assert "hashA" in fake._doc_id_to_path   # chroma untouched
    assert os.path.exists(path)               # file untouched


def test_hide_then_unhide_writes_distinct_audit_actions(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashA", "report.pdf")
    _install_fake_collection(monkeypatch, {"hashA": path})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    client.patch("/api/admin/files/hashA/flags", json={"hidden_by_admin": True})
    client.patch("/api/admin/files/hashA/flags", json={"hidden_by_admin": False})

    actions = [e["action"] for e in db.list_audit_log()]
    assert "FILE_HIDDEN" in actions
    assert "FILE_UNHIDDEN" in actions


def test_permanent_delete_is_distinct_from_hide(db, library_env, monkeypatch):
    """hidden=true must never be mistaken for (or substitute) a real delete."""
    path = _make_file(db, library_env, "hashA", "report.pdf")
    fake = _install_fake_collection(monkeypatch, {"hashA": path})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    client.patch("/api/admin/files/hashA/flags", json={"hidden_by_admin": True})
    # Row still exists (merely hidden) — this is the key distinction.
    assert any(f["doc_id"] == "hashA" for f in db.list_files())
    assert "hashA" in fake._doc_id_to_path


# ── Bulk actions ─────────────────────────────────────────────────────────────
def test_bulk_delete_independently_validates_each_id(db, library_env, monkeypatch):
    path_a = _make_file(db, library_env, "hashA", "a.pdf")
    path_b = _make_file(db, library_env, "hashB", "b.pdf")
    _install_fake_collection(monkeypatch, {"hashA": path_a, "hashB": path_b})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    r = client.post("/api/admin/files/bulk", json={
        "doc_ids": ["hashA", "hashB", "does-not-exist"],
        "action": "delete",
    })
    assert r.status_code == 200
    results = {x["doc_id"]: x for x in r.json()["results"]}
    assert results["hashA"]["ok"] is True
    assert results["hashB"]["ok"] is True
    assert results["does-not-exist"]["ok"] is False

    assert db.list_files() == []  # both real files gone
    assert not os.path.exists(path_a)
    assert not os.path.exists(path_b)


def test_bulk_hide_multiple_files(db, library_env, monkeypatch):
    path_a = _make_file(db, library_env, "hashA", "a.pdf")
    path_b = _make_file(db, library_env, "hashB", "b.pdf")
    _install_fake_collection(monkeypatch, {"hashA": path_a, "hashB": path_b})

    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)

    r = client.post("/api/admin/files/bulk", json={"doc_ids": ["hashA", "hashB"], "action": "hide"})
    assert all(x["ok"] for x in r.json()["results"])
    assert all(f["hidden_by_admin"] == 1 for f in db.list_files())
    # Bulk hide must not have deleted anything.
    assert len(db.list_files()) == 2


def test_bulk_action_rejects_empty_list():
    app = _admin_app()
    app.dependency_overrides[deps.require_admin] = _admin_user
    client = TestClient(app)
    r = client.post("/api/admin/files/bulk", json={"doc_ids": [], "action": "delete"})
    assert r.status_code == 422  # min_length=1 on the schema


# ── Uploader own-file deletion (Feature 8) ──────────────────────────────────
def test_uploader_can_delete_own_file(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashA", "a.pdf", uploaded_by="sambodh", is_public=True)
    _install_fake_collection(monkeypatch, {"hashA": path})

    app = _files_app()
    app.dependency_overrides[deps.require_uploader] = lambda: _uploader_user("sambodh")
    client = TestClient(app)

    r = client.delete("/api/files", params={"doc_id": "hashA"})
    assert r.status_code == 200
    assert db.list_files() == []


def test_uploader_cannot_delete_other_uploaders_file(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashD", "d.pdf", uploaded_by="girver", is_public=True)
    _install_fake_collection(monkeypatch, {"hashD": path})

    app = _files_app()
    app.dependency_overrides[deps.require_uploader] = lambda: _uploader_user("sambodh")
    client = TestClient(app)

    r = client.delete("/api/files", params={"doc_id": "hashD"})
    assert r.status_code == 403
    # Nothing was touched.
    assert any(f["doc_id"] == "hashD" for f in db.list_files())
    assert os.path.exists(path)


def test_admin_can_delete_any_uploaders_file_via_files_endpoint(db, library_env, monkeypatch):
    path = _make_file(db, library_env, "hashD", "d.pdf", uploaded_by="girver", is_public=True)
    _install_fake_collection(monkeypatch, {"hashD": path})

    app = _files_app()
    app.dependency_overrides[deps.require_uploader] = _admin_user
    client = TestClient(app)

    r = client.delete("/api/files", params={"doc_id": "hashD"})
    assert r.status_code == 200
    assert db.list_files() == []


def test_viewer_cannot_reach_delete_endpoint_at_all(db, library_env, monkeypatch):
    """require_uploader itself rejects viewers — this exercises the real
    dependency (not overridden) to confirm the role gate is in effect."""
    path = _make_file(db, library_env, "hashA", "a.pdf", uploaded_by="alice", is_public=True)
    _install_fake_collection(monkeypatch, {"hashA": path})

    app = FastAPI()
    app.include_router(files_router.router)
    app.dependency_overrides[deps.get_current_user] = lambda: _viewer_user()
    client = TestClient(app)

    r = client.delete("/api/files", params={"doc_id": "hashA"})
    assert r.status_code == 403
    assert any(f["doc_id"] == "hashA" for f in db.list_files())


def test_list_recent_uploads_scoped_by_acl(db, library_env, monkeypatch):
    """An uploader's 'recent uploads' view is bounded by the same ACL as
    everywhere else — not a global dump of every uploaded_by row."""
    depts = db.list_departments()
    hr = next(d for d in depts if d["name"] == "HR")
    sales = next(d for d in depts if d["name"] == "Sales")

    own_path = _make_file(db, library_env, "hashOwn", "own.pdf",
                           uploaded_by="sambodh", is_public=False, dept_ids=[sales["id"]])
    other_hr_path = _make_file(db, library_env, "hashHR", "hr_only.pdf",
                                uploaded_by="girver", is_public=False, dept_ids=[hr["id"]])
    _install_fake_collection(monkeypatch, {"hashOwn": own_path, "hashHR": other_hr_path})

    app = _files_app()
    app.dependency_overrides[deps.require_uploader] = lambda: _uploader_user("sambodh", dept_id=sales["id"])
    client = TestClient(app)

    r = client.get("/api/files/mine")
    doc_ids = {row["doc_id"] for row in r.json()}
    assert "hashOwn" in doc_ids
    assert "hashHR" not in doc_ids  # different department, not public — invisible


def test_recent_uploads_can_delete_flag_reflects_ownership(db, library_env, monkeypatch):
    depts = db.list_departments()
    sales = next(d for d in depts if d["name"] == "Sales")
    own_path = _make_file(db, library_env, "hashOwn", "own.pdf",
                           uploaded_by="sambodh", is_public=True)
    other_path = _make_file(db, library_env, "hashOther", "other.pdf",
                             uploaded_by="girver", is_public=True)
    _install_fake_collection(monkeypatch, {"hashOwn": own_path, "hashOther": other_path})

    app = _files_app()
    app.dependency_overrides[deps.require_uploader] = lambda: _uploader_user("sambodh", dept_id=sales["id"])
    client = TestClient(app)

    rows = {r["doc_id"]: r for r in client.get("/api/files/mine").json()}
    assert rows["hashOwn"]["can_delete"] is True
    assert rows["hashOther"]["can_delete"] is False
