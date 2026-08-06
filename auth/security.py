"""
security.py — password hashing
===============================
Uses PBKDF2-HMAC-SHA256 from the Python standard library.

Why not bcrypt: bcrypt needs a compiled wheel, which is one more thing that
can break on the Hawkins server (the same class of problem as the Python 3.14
/ pydantic-core wheel issue). hashlib.pbkdf2_hmac ships with Python, needs no
build step, and at 240k iterations is entirely adequate for an internal tool.

Stored format:  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

import hashlib
import hmac
import os

ALGORITHM  = "pbkdf2_sha256"
ITERATIONS = 240_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a plaintext password into a storable string."""
    if not password:
        raise ValueError("Password cannot be empty")
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Check a plaintext password against a stored hash.
    Returns False on any malformed stored value rather than raising —
    a corrupt row should fail the login, not crash the app.
    """
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    # constant-time compare — avoids leaking hash prefix via timing
    return hmac.compare_digest(dk.hex(), hash_hex)
