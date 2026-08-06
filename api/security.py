"""
api/security.py — JWT session tokens for the FastAPI layer
=============================================================
This is separate from auth/security.py (password hashing) on purpose:
that file protects passwords at rest, this one protects the session that
proves a browser already passed the login check.

Why JWT + httpOnly cookie instead of Streamlit's st.session_state:
  Streamlit kept the logged-in user server-side, tied to one script process.
  A stateless Next.js frontend talking to a separate FastAPI process has no
  equivalent — the browser has to carry proof of identity on every request.
  A signed, short-lived JWT in an httpOnly cookie is the standard answer:
  the browser can't read or tamper with it (no XSS token theft via JS), and
  the server doesn't need a session table to validate it.

The token payload intentionally carries only `sub` (username) and `role`.
Everything else (dept_id, dept_name, is_active) is re-fetched from auth.db
on every request in deps.py — the token is proof of *identity*, not a cache
of permissions, so an admin revoking a user's access takes effect on their
very next request instead of only after the token expires. This mirrors the
exact guarantee the old ALLOWED_DOC_IDS recompute-every-rerun comment in
app.py called out.
"""

import os
import time
import jwt  # PyJWT

# In production set HAWKINS_JWT_SECRET as a real env var. This fallback exists
# so the app doesn't crash on a fresh clone, but every server restart with the
# fallback active invalidates all existing sessions — document that trade-off
# to whoever deploys this, same as the "change the default admin password"
# note in auth/db.py.
SECRET_KEY   = os.environ.get("HAWKINS_JWT_SECRET", "dev-only-insecure-secret-change-me")
ALGORITHM    = "HS256"
TOKEN_TTL_S  = 60 * 60 * 12   # 12 hours — long enough for a workday, short
                              # enough that a stolen cookie doesn't live forever


def create_access_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + TOKEN_TTL_S,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Returns the payload dict, or None if the token is missing/expired/tampered."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
