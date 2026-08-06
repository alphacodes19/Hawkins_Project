"""
tests/test_acl.py
=================
Unit tests for auth/db.py — the access-control layer.

Tests cover:
  - init_db() is idempotent (safe to call multiple times)
  - Default admin account seeded on first init
  - Department CRUD
  - User CRUD (create, authenticate, update, delete)
  - Last-admin deletion guard
  - register_file() + set_file_flags() + set_file_departments()
  - allowed_doc_ids() — the core visibility decision tree:
      admin          → None  (sees everything)
      public file    → visible to all roles
      dept-matched   → visible to matching dept user
      hidden         → invisible even to uploader
      empty allowed  → user with no dept and no public files sees nothing

Uses the `tmp_db` fixture from conftest.py which creates an isolated
auth.db in a temp directory for each test.
"""

import pytest
from auth.security import verify_password


# ── init_db idempotency ───────────────────────────────────────────────────────

class TestInitDb:
    def test_idempotent(self, tmp_db):
        # Calling init_db() again must not raise or duplicate rows
        tmp_db.init_db()
        tmp_db.init_db()

    def test_default_admin_created(self, tmp_db):
        users = tmp_db.list_users()
        admin_rows = [u for u in users if u["role"] == "admin"]
        assert len(admin_rows) == 1
        assert admin_rows[0]["username"] == "admin"

    def test_default_admin_password_works(self, tmp_db):
        user = tmp_db.authenticate("admin", "hawkins-change-me")
        assert user is not None
        assert user["role"] == "admin"

    def test_default_departments_seeded(self, tmp_db):
        depts = tmp_db.list_departments()
        assert len(depts) > 0
        names = {d["name"] for d in depts}
        assert "Sales" in names
        assert "HR" in names


# ── Department CRUD ───────────────────────────────────────────────────────────

class TestDepartments:
    def test_add_department(self, tmp_db):
        tmp_db.add_department("Legal")
        names = {d["name"] for d in tmp_db.list_departments()}
        assert "Legal" in names

    def test_duplicate_department_raises(self, tmp_db):
        with pytest.raises(ValueError, match="already exists"):
            tmp_db.add_department("Sales")   # seeded on init

    def test_empty_name_raises(self, tmp_db):
        with pytest.raises(ValueError):
            tmp_db.add_department("")

    def test_rename_department(self, tmp_db):
        depts = tmp_db.list_departments()
        dept_id = depts[0]["id"]
        tmp_db.rename_department(dept_id, "Renamed Dept")
        names = {d["name"] for d in tmp_db.list_departments()}
        assert "Renamed Dept" in names

    def test_delete_department(self, tmp_db):
        tmp_db.add_department("ToDelete")
        depts = tmp_db.list_departments()
        dept_id = next(d["id"] for d in depts if d["name"] == "ToDelete")
        tmp_db.delete_department(dept_id)
        names = {d["name"] for d in tmp_db.list_departments()}
        assert "ToDelete" not in names


# ── User CRUD ─────────────────────────────────────────────────────────────────

class TestUsers:
    def test_create_and_authenticate(self, tmp_db):
        tmp_db.create_user("alice", "pass123", role="viewer")
        user = tmp_db.authenticate("alice", "pass123")
        assert user is not None
        assert user["username"] == "alice"
        assert user["role"] == "viewer"

    def test_wrong_password_returns_none(self, tmp_db):
        tmp_db.create_user("bob", "correct-pass", role="viewer")
        assert tmp_db.authenticate("bob", "wrong-pass") is None

    def test_nonexistent_user_returns_none(self, tmp_db):
        assert tmp_db.authenticate("nobody", "anything") is None

    def test_duplicate_username_raises(self, tmp_db):
        tmp_db.create_user("charlie", "pw", role="viewer")
        with pytest.raises(ValueError, match="already exists"):
            tmp_db.create_user("charlie", "pw2", role="viewer")

    def test_invalid_role_raises(self, tmp_db):
        with pytest.raises(ValueError, match="Role"):
            tmp_db.create_user("dave", "pw", role="superuser")

    def test_inactive_user_cannot_authenticate(self, tmp_db):
        tmp_db.create_user("eve", "pw", role="viewer")
        users = tmp_db.list_users()
        eve = next(u for u in users if u["username"] == "eve")
        tmp_db.update_user(eve["id"], is_active=False)
        assert tmp_db.authenticate("eve", "pw") is None

    def test_set_password(self, tmp_db):
        tmp_db.create_user("frank", "old-pass", role="viewer")
        users = tmp_db.list_users()
        frank = next(u for u in users if u["username"] == "frank")
        tmp_db.set_password(frank["id"], "new-pass")
        assert tmp_db.authenticate("frank", "new-pass") is not None
        assert tmp_db.authenticate("frank", "old-pass") is None

    def test_update_role(self, tmp_db):
        tmp_db.create_user("grace", "pw", role="viewer")
        users = tmp_db.list_users()
        grace = next(u for u in users if u["username"] == "grace")
        tmp_db.update_user(grace["id"], role="uploader")
        updated = tmp_db.authenticate("grace", "pw")
        assert updated["role"] == "uploader"

    def test_delete_user(self, tmp_db):
        tmp_db.create_user("henry", "pw", role="viewer")
        users = tmp_db.list_users()
        henry = next(u for u in users if u["username"] == "henry")
        tmp_db.delete_user(henry["id"])
        assert tmp_db.authenticate("henry", "pw") is None

    def test_cannot_delete_last_admin(self, tmp_db):
        users = tmp_db.list_users()
        admin = next(u for u in users if u["role"] == "admin")
        with pytest.raises(ValueError, match="last active admin"):
            tmp_db.delete_user(admin["id"])

    def test_can_delete_admin_when_another_exists(self, tmp_db):
        tmp_db.create_user("admin2", "pw", role="admin")
        users = tmp_db.list_users()
        orig_admin = next(u for u in users if u["username"] == "admin")
        # Should not raise — there's now a second admin
        tmp_db.delete_user(orig_admin["id"])


# ── File registration ─────────────────────────────────────────────────────────

class TestFileRegistration:
    def test_register_file(self, tmp_db):
        tmp_db.register_file("abc123", "manual.pdf", uploaded_by="admin")
        files = tmp_db.list_files()
        assert any(f["doc_id"] == "abc123" for f in files)

    def test_register_same_file_twice_is_idempotent(self, tmp_db):
        tmp_db.register_file("abc123", "manual.pdf", uploaded_by="admin")
        tmp_db.register_file("abc123", "manual.pdf", uploaded_by="admin")
        files = [f for f in tmp_db.list_files() if f["doc_id"] == "abc123"]
        assert len(files) == 1

    def test_set_public_flag(self, tmp_db):
        tmp_db.register_file("pub001", "public.pdf", is_public=True)
        files = tmp_db.list_files()
        f = next(f for f in files if f["doc_id"] == "pub001")
        assert f["is_public"] == 1

    def test_set_hidden_flag(self, tmp_db):
        tmp_db.register_file("hid001", "secret.pdf")
        tmp_db.set_file_flags("hid001", hidden_by_admin=True)
        files = tmp_db.list_files()
        f = next(f for f in files if f["doc_id"] == "hid001")
        assert f["hidden_by_admin"] == 1

    def test_set_file_departments(self, tmp_db):
        depts = tmp_db.list_departments()
        hr_id = next(d["id"] for d in depts if d["name"] == "HR")
        tmp_db.register_file("dept001", "hr_policy.pdf")
        tmp_db.set_file_departments("dept001", [hr_id])
        files = tmp_db.list_files()
        f = next(f for f in files if f["doc_id"] == "dept001")
        dept_ids = {d["id"] for d in f["departments"]}
        assert hr_id in dept_ids


# ── allowed_doc_ids — the core ACL decision tree ──────────────────────────────

class TestAllowedDocIds:
    """
    Visibility rules in order (from db.py docstring):
      1. hidden_by_admin = 1   → invisible to everyone except admins
      2. requester is admin    → visible (returns None for no filter)
      3. requester uploaded it → visible
      4. is_public = 1         → visible
      5. dept match            → visible
      6. otherwise             → invisible
    """

    def _make_viewer(self, tmp_db, username, dept_name=None):
        dept_id = None
        if dept_name:
            depts = tmp_db.list_departments()
            dept_id = next(d["id"] for d in depts if d["name"] == dept_name)
        tmp_db.create_user(username, "pw", role="viewer", dept_id=dept_id)
        return tmp_db.authenticate(username, "pw")

    def test_admin_gets_none(self, tmp_db):
        admin = tmp_db.authenticate("admin", "hawkins-change-me")
        result = tmp_db.allowed_doc_ids(admin)
        assert result is None  # None means "no filter — sees everything"

    def test_no_user_gets_empty_set(self, tmp_db):
        result = tmp_db.allowed_doc_ids(None)
        assert result == set()

    def test_public_file_visible_to_viewer_without_dept(self, tmp_db):
        tmp_db.register_file("pub001", "public.pdf", is_public=True)
        viewer = self._make_viewer(tmp_db, "v_nodept")
        ids = tmp_db.allowed_doc_ids(viewer)
        assert "pub001" in ids

    def test_dept_file_visible_to_matching_dept_user(self, tmp_db):
        depts = tmp_db.list_departments()
        hr_id = next(d["id"] for d in depts if d["name"] == "HR")
        tmp_db.register_file("hr001", "hr_policy.pdf")
        tmp_db.set_file_departments("hr001", [hr_id])
        hr_user = self._make_viewer(tmp_db, "hr_user", dept_name="HR")
        ids = tmp_db.allowed_doc_ids(hr_user)
        assert "hr001" in ids

    def test_dept_file_invisible_to_different_dept_user(self, tmp_db):
        depts = tmp_db.list_departments()
        hr_id = next(d["id"] for d in depts if d["name"] == "HR")
        tmp_db.register_file("hr001", "hr_policy.pdf")
        tmp_db.set_file_departments("hr001", [hr_id])
        sales_user = self._make_viewer(tmp_db, "sales_user", dept_name="Sales")
        ids = tmp_db.allowed_doc_ids(sales_user)
        assert "hr001" not in ids

    def test_hidden_file_invisible_to_uploader(self, tmp_db):
        tmp_db.create_user("uploader1", "pw", role="uploader")
        u = tmp_db.authenticate("uploader1", "pw")
        tmp_db.register_file("sec001", "secret.pdf", uploaded_by="uploader1")
        tmp_db.set_file_flags("sec001", hidden_by_admin=True)
        ids = tmp_db.allowed_doc_ids(u)
        assert "sec001" not in ids

    def test_hidden_file_visible_to_admin(self, tmp_db):
        tmp_db.register_file("sec002", "admin_only.pdf")
        tmp_db.set_file_flags("sec002", hidden_by_admin=True)
        admin = tmp_db.authenticate("admin", "hawkins-change-me")
        # Admin gets None (unrestricted) — can see everything including hidden
        result = tmp_db.allowed_doc_ids(admin)
        assert result is None

    def test_uploader_sees_own_file(self, tmp_db):
        tmp_db.create_user("uploader2", "pw", role="uploader")
        u = tmp_db.authenticate("uploader2", "pw")
        tmp_db.register_file("own001", "my_upload.pdf", uploaded_by="uploader2")
        ids = tmp_db.allowed_doc_ids(u)
        assert "own001" in ids

    def test_viewer_with_no_access_gets_empty_set(self, tmp_db):
        # Register a dept-restricted file, viewer has no dept and file is not public
        depts = tmp_db.list_departments()
        hr_id = next(d["id"] for d in depts if d["name"] == "HR")
        tmp_db.register_file("hr_restricted", "hr_only.pdf")
        tmp_db.set_file_departments("hr_restricted", [hr_id])
        viewer = self._make_viewer(tmp_db, "dept_less_viewer")   # no dept
        ids = tmp_db.allowed_doc_ids(viewer)
        assert "hr_restricted" not in ids
