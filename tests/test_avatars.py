"""
test_avatars.py — profile photo storage and API (Feature 1)
================================================================
Covers:
  - save_avatar(): accepts real JPEG/PNG, rejects non-image bytes, rejects
    oversized files, never trusts the filename for path or extension
  - delete_avatar_file(): removes only files actually inside AVATAR_DIR
  - /api/auth/avatar endpoints: upload, replace (old file cleaned up),
    remove, and that a user can only ever affect their own account
    (there's no user_id parameter on the write endpoints at all — the
    authenticated session is the only source of "which account")
"""

import io
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api import deps
from api.routers import auth as auth_router


def _png_bytes(size=(64, 64), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_app(current_user: dict):
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[deps.get_current_user] = lambda: current_user
    return app


@pytest.fixture
def db(tmp_db):
    return tmp_db


@pytest.fixture
def avatar_env(tmp_path, monkeypatch):
    """Redirects AVATAR_DIR into a temp directory for the duration of the test."""
    from api import avatars
    tmp_avatar_dir = tmp_path / "avatars"
    tmp_avatar_dir.mkdir()
    monkeypatch.setattr(avatars, "AVATAR_DIR", str(tmp_avatar_dir))
    return avatars


def _user_row(db, username):
    return next(u for u in db.list_users() if u["username"] == username)


# ── save_avatar() unit behavior ─────────────────────────────────────────────
def test_save_avatar_accepts_valid_png(avatar_env):
    path = avatar_env.save_avatar(1, _png_bytes())
    assert os.path.exists(path)
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(avatar_env.AVATAR_DIR)


def test_save_avatar_rejects_non_image_bytes(avatar_env):
    with pytest.raises(avatar_env.InvalidAvatarError):
        avatar_env.save_avatar(1, b"not an image, just text pretending to be one" * 10)


def test_save_avatar_rejects_empty_file(avatar_env):
    with pytest.raises(avatar_env.InvalidAvatarError):
        avatar_env.save_avatar(1, b"")


def test_save_avatar_rejects_oversized_file(avatar_env, monkeypatch):
    monkeypatch.setattr(avatar_env, "MAX_AVATAR_BYTES", 100)
    with pytest.raises(avatar_env.InvalidAvatarError):
        avatar_env.save_avatar(1, _png_bytes(size=(512, 512)))


def test_save_avatar_ignores_client_filename_entirely(avatar_env):
    """The API signature doesn't even accept a filename — this just proves
    the generated path is derived purely from user_id + a random token."""
    path1 = avatar_env.save_avatar(7, _png_bytes())
    path2 = avatar_env.save_avatar(7, _png_bytes())
    assert path1 != path2  # replacing doesn't overwrite in place
    assert "user_7_" in os.path.basename(path1)


def test_save_avatar_rejects_path_traversal_style_polyglot(avatar_env):
    """A file that starts with valid-looking bytes but isn't a decodable
    image (e.g. a truncated/crafted polyglot) must be rejected outright —
    there is no filename involved in storage at all, so traversal via
    filename isn't reachable, but malformed image bytes still must fail
    the decode step rather than being written as-is."""
    junk = b"\x89PNG\r\n\x1a\n" + os.urandom(200)  # PNG magic bytes, garbage body
    with pytest.raises(avatar_env.InvalidAvatarError):
        avatar_env.save_avatar(1, junk)


def test_delete_avatar_file_only_removes_files_inside_avatar_dir(avatar_env, tmp_path):
    outside = tmp_path / "not_an_avatar.txt"
    outside.write_text("do not delete me")
    avatar_env.delete_avatar_file(str(outside))
    assert outside.exists()  # untouched — outside AVATAR_DIR


def test_delete_avatar_file_removes_real_avatar(avatar_env):
    path = avatar_env.save_avatar(1, _png_bytes())
    avatar_env.delete_avatar_file(path)
    assert not os.path.exists(path)


def test_delete_avatar_file_none_is_a_noop(avatar_env):
    avatar_env.delete_avatar_file(None)  # must not raise


# ── API endpoints ────────────────────────────────────────────────────────
def test_upload_avatar_sets_has_avatar(db, avatar_env):
    alice_row = _user_row(db, "admin")  # seeded default admin
    client = TestClient(_make_app(alice_row))

    r = client.post("/api/auth/avatar", files={"file": ("photo.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert r.json()["has_avatar"] is True


def test_upload_avatar_rejects_invalid_file(db, avatar_env):
    admin_row = _user_row(db, "admin")
    client = TestClient(_make_app(admin_row))

    r = client.post("/api/auth/avatar", files={"file": ("photo.png", b"garbage", "image/png")})
    assert r.status_code == 400
    assert db.get_user_by_id(admin_row["id"])["avatar_path"] is None


def test_replace_avatar_deletes_old_file(db, avatar_env):
    admin_row = _user_row(db, "admin")
    client = TestClient(_make_app(admin_row))

    client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    first_path = db.get_user_by_id(admin_row["id"])["avatar_path"]
    assert os.path.exists(first_path)

    client.post("/api/auth/avatar", files={"file": ("b.png", _png_bytes(color=(0, 0, 200)), "image/png")})
    second_path = db.get_user_by_id(admin_row["id"])["avatar_path"]

    assert second_path != first_path
    assert not os.path.exists(first_path)  # old file cleaned up
    assert os.path.exists(second_path)


def test_remove_avatar_clears_and_deletes_file(db, avatar_env):
    admin_row = _user_row(db, "admin")
    client = TestClient(_make_app(admin_row))

    client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    path = db.get_user_by_id(admin_row["id"])["avatar_path"]

    r = client.delete("/api/auth/avatar")
    assert r.status_code == 200
    assert r.json()["has_avatar"] is False
    assert db.get_user_by_id(admin_row["id"])["avatar_path"] is None
    assert not os.path.exists(path)


def test_avatar_upload_only_ever_affects_the_authenticated_users_own_account(db, avatar_env):
    """
    There's no user_id in the POST/DELETE avatar request at all — the
    backend always acts on the id from the session (Depends(get_current_user)).
    This proves a second account is untouched by the first account's upload.
    """
    db.create_user("bob", "pw12345678", role="viewer")
    admin_row = _user_row(db, "admin")
    bob_row = _user_row(db, "bob")

    admin_client = TestClient(_make_app(admin_row))
    admin_client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})

    assert db.get_user_by_id(admin_row["id"])["avatar_path"] is not None
    assert db.get_user_by_id(bob_row["id"])["avatar_path"] is None


def test_get_avatar_404_when_none_set(db, avatar_env):
    admin_row = _user_row(db, "admin")
    client = TestClient(_make_app(admin_row))
    r = client.get(f"/api/auth/avatar/{admin_row['id']}")
    assert r.status_code == 404
