"""
api/avatars.py — profile photo storage
==========================================
Security posture, deliberately conservative because this accepts arbitrary
bytes from any authenticated user:

  - The client-supplied filename is NEVER used to build a path or to decide
    the file extension. The destination filename is generated server-side
    (user id + random token), so nothing about the request controls where
    on disk the file ends up — no path traversal surface at all.
  - The upload is validated by actually decoding it as an image (Pillow),
    not by trusting the browser's reported Content-Type or the filename's
    extension. Only JPEG/PNG/WEBP are accepted.
  - The image is re-encoded (not just copied) before being written to disk.
    Re-encoding drops any non-pixel payload a crafted file might carry
    alongside valid image data, and caps dimensions so one huge upload
    can't blow up disk usage.
"""

import os
import secrets
from io import BytesIO

import config

AVATAR_DIR = os.path.join(config.BASE_DIR, "data", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5 MB, enforced before any decoding is attempted
MAX_DIMENSION = 512                 # long edge, px — plenty for an avatar

_FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


class InvalidAvatarError(ValueError):
    """Raised for any upload that fails validation. Message is safe to show
    the user directly — never includes internal paths or stack detail."""


def save_avatar(user_id: int, raw_bytes: bytes) -> str:
    """Validates and stores an avatar. Returns the new file's absolute path."""
    if not raw_bytes:
        raise InvalidAvatarError("The uploaded file is empty.")
    if len(raw_bytes) > MAX_AVATAR_BYTES:
        raise InvalidAvatarError("Image must be smaller than 5 MB.")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as e:
        raise RuntimeError(
            "Pillow is required for profile photos but is not installed."
        ) from e

    try:
        probe = Image.open(BytesIO(raw_bytes))
        probe.verify()  # structural check; the file object is unusable after this
    except (UnidentifiedImageError, OSError, ValueError):
        raise InvalidAvatarError("File is not a valid image.")

    fmt = (probe.format or "").upper()
    if fmt not in _FORMAT_EXT:
        raise InvalidAvatarError("Only JPEG, PNG, and WEBP images are supported.")

    # Re-open — verify() leaves the original handle unusable for further ops.
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise InvalidAvatarError("File is not a valid image.")

    if fmt == "JPEG":
        img = img.convert("RGB")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    ext = _FORMAT_EXT[fmt]
    filename = f"user_{user_id}_{secrets.token_hex(8)}.{ext}"
    dest_path = os.path.join(AVATAR_DIR, filename)

    save_kwargs = {"quality": 88, "optimize": True} if fmt == "JPEG" else {}
    img.save(dest_path, format=fmt, **save_kwargs)
    return dest_path


def delete_avatar_file(avatar_path):
    """Best-effort cleanup of a previous avatar file. Silently does nothing
    if the path is empty, already gone, or (defensively) doesn't actually
    resolve inside AVATAR_DIR — the last check means a corrupted/legacy
    avatar_path value can never be used to delete an arbitrary file."""
    if not avatar_path:
        return
    try:
        real = os.path.realpath(avatar_path)
        if os.path.dirname(real) != os.path.realpath(AVATAR_DIR):
            return
        if os.path.exists(real):
            os.remove(real)
    except OSError:
        pass
