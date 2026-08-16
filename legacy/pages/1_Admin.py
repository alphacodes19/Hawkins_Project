"""
1_Admin.py — administration panel
==================================
Streamlit auto-discovers files in pages/ and puts them in the sidebar nav, so
this page is REACHABLE by anyone who is logged in, whatever their role. The
require_admin() call on line one is what actually protects it — the page is not
hidden, it is gated. Do not remove it.
"""

import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import streamlit as st

from auth import db as authdb
from auth import ui as authui

st.set_page_config(page_title="Admin Control Panel · Hawkins Data Archive", layout="wide")

USER = authui.require_admin()

st.title("Admin Control Panel")
st.caption(f"Signed in as {USER['username']}")

with st.sidebar:
    authui.render_account_box()
    st.divider()
    st.page_link("app.py", label="Back to Search Panel")

tab_files, tab_users, tab_depts = st.tabs(["File visibility", "Users", "Departments"])


# ═════════════════════════════════════════════════════════════════════════════
# FILE VISIBILITY
# ═════════════════════════════════════════════════════════════════════════════
with tab_files:
    st.subheader("File visibility")
    st.caption(
        "Every indexed file and who can reach it. Changes apply to the next "
        "query — no re-indexing."
    )

    departments = authdb.list_departments()
    dept_by_id  = {d["id"]: d["name"] for d in departments}
    dept_by_name = {d["name"]: d["id"] for d in departments}
    files = authdb.list_files()

    if not files:
        st.info(
            "No files registered yet. If you have an existing ChromaDB index, "
            "run `python -m scripts.migrate_acl` to register it."
        )
    else:
        search = st.text_input("Filter by filename", placeholder="e.g. Presstek")
        shown = [f for f in files if search.lower() in f["source"].lower()] if search else files

        st.caption(f"{len(shown)} of {len(files)} files")

        for f in shown:
            current = [d["name"] for d in f["departments"]]

            if f["hidden_by_admin"]:
                status = "Hidden from everyone"
            elif f["is_public"]:
                status = "Public"
            elif current:
                status = ", ".join(current)
            else:
                status = "Admins only (untagged)"

            with st.expander(f"{f['source']}  ·  {status}"):
                st.caption(f"doc_id: `{f['doc_id']}`")
                if f["uploaded_by"]:
                    st.caption(f"Uploaded by: {f['uploaded_by']}")

                with st.form(f"file_{f['id']}"):
                    picked = st.multiselect(
                        "Visible to departments",
                        options=list(dept_by_name.keys()),
                        default=[c for c in current if c in dept_by_name],
                    )
                    c1, c2 = st.columns(2)
                    with c1:
                        is_public = st.checkbox(
                            "Visible to everyone",
                            value=bool(f["is_public"]),
                            help="Overrides the department list.",
                        )
                    with c2:
                        hidden = st.checkbox(
                            "Hide from everyone",
                            value=bool(f["hidden_by_admin"]),
                            help="Kill switch. Overrides everything above. "
                                 "The file stays indexed but becomes unreachable "
                                 "for all non-admin users.",
                        )
                    saved = st.form_submit_button("Save", type="primary")

                if saved:
                    authdb.set_file_departments(f["doc_id"], [dept_by_name[p] for p in picked])
                    authdb.set_file_flags(f["doc_id"], is_public=is_public,
                                          hidden_by_admin=hidden)
                    st.success("Saved.")
                    st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# USERS
# ═════════════════════════════════════════════════════════════════════════════
with tab_users:
    st.subheader("Users")

    departments  = authdb.list_departments()
    dept_by_name = {d["name"]: d["id"] for d in departments}
    NO_DEPT      = "— none —"

    with st.expander("Create a user"):
        with st.form("create_user", clear_on_submit=True):
            u_name = st.text_input("Username")
            u_pw   = st.text_input("Password", type="password")
            c1, c2 = st.columns(2)
            with c1:
                u_role = st.selectbox("Role", authdb.ROLES, index=authdb.ROLES.index("viewer"))
            with c2:
                u_dept = st.selectbox("Department", [NO_DEPT] + list(dept_by_name.keys()))
            create = st.form_submit_button("Create user", type="primary")

        if create:
            if len(u_pw) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                try:
                    authdb.create_user(
                        u_name, u_pw, role=u_role,
                        dept_id=None if u_dept == NO_DEPT else dept_by_name[u_dept],
                    )
                    st.success(f"Created '{u_name}'.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    st.divider()

    for u in authdb.list_users():
        label = f"{u['username']}  ·  {u['role']}"
        if u["dept_name"]:
            label += f"  ·  {u['dept_name']}"
        if not u["is_active"]:
            label += "  ·  DISABLED"

        with st.expander(label):
            with st.form(f"user_{u['id']}"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    role = st.selectbox("Role", authdb.ROLES,
                                        index=authdb.ROLES.index(u["role"]),
                                        key=f"role_{u['id']}")
                with c2:
                    names   = [NO_DEPT] + list(dept_by_name.keys())
                    default = u["dept_name"] if u["dept_name"] in dept_by_name else NO_DEPT
                    dept    = st.selectbox("Department", names,
                                           index=names.index(default),
                                           key=f"dept_{u['id']}")
                with c3:
                    active = st.checkbox("Active", value=bool(u["is_active"]),
                                         key=f"act_{u['id']}")
                new_pw = st.text_input("Reset password (leave blank to keep current)",
                                       type="password", key=f"pw_{u['id']}")
                saved = st.form_submit_button("Save")

            if saved:
                authdb.update_user(
                    u["id"], role=role,
                    dept_id=None if dept == NO_DEPT else dept_by_name[dept],
                    is_active=active,
                )
                if new_pw:
                    if len(new_pw) < 8:
                        st.error("Password must be at least 8 characters.")
                    else:
                        authdb.set_password(u["id"], new_pw)
                st.success("Saved.")
                st.rerun()

            if st.button("Delete user", key=f"del_{u['id']}"):
                try:
                    authdb.delete_user(u["id"])
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ═════════════════════════════════════════════════════════════════════════════
# DEPARTMENTS
# ═════════════════════════════════════════════════════════════════════════════
with tab_depts:
    st.subheader("Departments")
    st.caption(
        "The seeded list is a placeholder. Replace it with Hawkins' real "
        "structure — nothing in the code depends on these names."
    )

    with st.form("add_dept", clear_on_submit=True):
        new_name = st.text_input("New department name")
        if st.form_submit_button("Add", type="primary"):
            try:
                authdb.add_department(new_name)
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.divider()

    for d in authdb.list_departments():
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            renamed = st.text_input("Name", value=d["name"], key=f"dn_{d['id']}",
                                    label_visibility="collapsed")
        with c2:
            if st.button("Rename", key=f"dr_{d['id']}", use_container_width=True):
                try:
                    authdb.rename_department(d["id"], renamed)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        with c3:
            if st.button("Delete", key=f"dd_{d['id']}", use_container_width=True):
                authdb.delete_department(d["id"])
                st.rerun()

    st.caption(
        "Deleting a department removes it from every file it was tagged on, and "
        "unassigns any user who belonged to it. Those users keep their accounts "
        "but lose department-based access until reassigned."
    )