"""
api/deps.py — request-scoped dependencies
============================================
The equivalent of auth/ui.py's require_login() / require_admin(), rewritten
for a stateless request/response cycle instead of a Streamlit script rerun.
"""

from fastapi import Cookie, Depends, Header, HTTPException, status

from auth import db as authdb
from api.security import decode_access_token

COOKIE_NAME = "hawkins_session"


def _extract_token(hawkins_session: str | None, authorization: str | None) -> str | None:
    """Cookie first (browser flow), Authorization: Bearer header as a fallback
    (useful for curl/Postman while testing the API directly)."""
    if hawkins_session:
        return hawkins_session
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return None


def get_current_user(
    hawkins_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """
    Resolves the session token to a live user record.

    Re-reads from auth.db on every call rather than trusting the token's
    embedded role — a role change or account deactivation by an admin must
    take effect on the user's very next request, not after their token
    expires up to 12 hours later.
    """
    token = _extract_token(hawkins_session, authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid")

    users = authdb.list_users()
    user = next((u for u in users if u["username"] == payload["sub"]), None)
    if not user or not user["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled")

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Use as `user: dict = Depends(require_admin)` on any admin-only route."""
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_uploader(user: dict = Depends(get_current_user)) -> dict:
    """Admins and uploaders — matches auth/ui.py's can_upload()."""
    if user.get("role") not in ("admin", "uploader"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Upload access required")
    return user
