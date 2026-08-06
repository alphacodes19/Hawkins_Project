"""
ui.py — Streamlit login gate and session handling
==================================================
WHAT THIS IS AND IS NOT

This gives every user their own login, their own department, and their own view
of the corpus. In normal use it reliably keeps Sales out of HR's documents.

It is NOT a security boundary. Streamlit runs the whole script in one process
with one ChromaDB handle. Anyone who can reach the machine's shell can open
chroma_db/ directly and read every document, regardless of what this file says.
Anyone who can edit the source can bypass the gate entirely.

That is an ordinary and acceptable trade-off for an internal tool behind a
company firewall — but it should be stated to whoever signs off on it, not
assumed. If the requirement is "confidential documents must be cryptographically
inaccessible to unauthorised staff", this architecture does not deliver that and
no amount of patching Streamlit will make it.
"""

import os
import streamlit as st

from auth import db as authdb

SESSION_KEY = "auth_user"

# ── Logo (shared with app.py — loaded once from static/ to keep source readable)
_UI_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
_LOGO_B64  = open(os.path.join(_UI_DIR, "static", "hawkins_logo_b64.txt")).read()


def current_user():
    """The logged-in user dict, or None."""
    return st.session_state.get(SESSION_KEY)


def is_admin() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "admin")


def can_upload() -> bool:
    u = current_user()
    return bool(u and u.get("role") in ("admin", "uploader"))


def logout():
    st.session_state.pop(SESSION_KEY, None)
    # Clear anything user-specific so the next login doesn't inherit it
    for key in ("messages", "last_chunks", "prefill_question"):
        st.session_state.pop(key, None)


def _render_login_form():
    # ── Centered login card ───────────────────────────────────────────────────
    _, col, _ = st.columns([1, 2, 1])
    with col:
        # Logo
        st.markdown(
            f"""<div style="text-align:center; margin-bottom:16px;">
                <img src="data:image/png;base64,{_LOGO_B64}"
                     style="width:160px; height:auto;">
            </div>""",
            unsafe_allow_html=True,
        )
        # Title
        st.markdown(
            "<h2 style='text-align:center; margin-bottom:4px;'>"
            "Hawkins Data Archive</h2>"
            "<p style='text-align:center; color:#888; font-size:13px; margin-bottom:24px;'>"
            "Sign in to continue</p>",
            unsafe_allow_html=True,
        )
        # Login form
        with st.form("login_form"):
            username  = st.text_input("Username")
            password  = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Sign In", type="primary", use_container_width=True
            )

        if submitted:
            user = authdb.authenticate(username, password)
            if user:
                st.session_state[SESSION_KEY] = user
                st.rerun()
            else:
                st.error("Invalid username or password.")


def require_login():
    """
    Gate the page. Returns the user dict, or halts the script.

    st.stop() raises internally, so nothing below the call in the calling module
    executes for an anonymous visitor.
    """
    authdb.init_db()

    user = current_user()
    if user is None:
        _render_login_form()
        st.stop()

    return user


def require_admin():
    """Gate an admin-only page. Halts with a message for anyone else."""
    user = require_login()
    if user.get("role") != "admin":
        st.error("You do not have permission to view this page.")
        st.caption("Contact your administrator if you believe this is a mistake.")
        st.stop()
    return user


def render_account_box():
    """Sidebar account panel: who you are, change password, sign out."""
    user = current_user()
    if not user:
        return

    dept = user.get("dept_name") or "— no department —"
    st.markdown(f"**{user['username']}**")
    st.caption(f"{user['role'].title()} · {dept}")

    with st.expander("Change password"):
        with st.form("change_pw"):
            old  = st.text_input("Current password", type="password")
            new1 = st.text_input("New password", type="password")
            new2 = st.text_input("Confirm new password", type="password")
            go   = st.form_submit_button("Update password")
        if go:
            if not authdb.authenticate(user["username"], old):
                st.error("Current password is incorrect.")
            elif len(new1) < 8:
                st.error("New password must be at least 8 characters.")
            elif new1 != new2:
                st.error("New passwords do not match.")
            else:
                authdb.set_password(user["id"], new1)
                st.success("Password updated.")

    if st.button("Sign out", use_container_width=True):
        logout()
        st.rerun()