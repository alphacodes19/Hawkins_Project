from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import require_admin
from api.schemas import (
    BulkFileActionRequest,
    DepartmentCreate,
    DepartmentRename,
    FileDeptUpdate,
    FileFlagsUpdate,
    UserCreate,
    UserUpdate,
)
from auth import db as authdb

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ── Departments ──────────────────────────────────────────────────────────────
@router.get("/departments")
def list_departments():
    return authdb.list_departments()


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def add_department(body: DepartmentCreate, user: dict = Depends(require_admin)):
    try:
        authdb.add_department(body.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    authdb.record_audit(
        user["username"], "DEPARTMENT_CREATED", "department", target_id=body.name,
        description=f"Created department '{body.name}'", after={"name": body.name},
    )
    return {"ok": True}


@router.patch("/departments/{dept_id}")
def rename_department(dept_id: int, body: DepartmentRename, user: dict = Depends(require_admin)):
    before = next((d for d in authdb.list_departments() if d["id"] == dept_id), None)
    try:
        authdb.rename_department(dept_id, body.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    authdb.record_audit(
        user["username"], "DEPARTMENT_RENAMED", "department", target_id=dept_id,
        description=f"Renamed department '{before['name'] if before else dept_id}' -> '{body.name}'",
        before=before, after={"name": body.name},
    )
    return {"ok": True}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: int, user: dict = Depends(require_admin)):
    before = next((d for d in authdb.list_departments() if d["id"] == dept_id), None)
    authdb.delete_department(dept_id)
    authdb.record_audit(
        user["username"], "DEPARTMENT_DELETED", "department", target_id=dept_id,
        description=f"Deleted department '{before['name'] if before else dept_id}'",
        before=before,
    )
    return {"ok": True}


# ── Users ────────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users():
    # Strip the raw filesystem path before it ever reaches the admin UI —
    # the avatar feature only needs "does this user have a photo", not
    # where it lives on disk.
    out = []
    for u in authdb.list_users():
        u = dict(u)
        u["has_avatar"] = bool(u.pop("avatar_path", None))
        out.append(u)
    return out


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, user: dict = Depends(require_admin)):
    try:
        authdb.create_user(body.username, body.password, role=body.role, dept_id=body.dept_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    authdb.record_audit(
        user["username"], "USER_CREATED", "user", target_id=body.username,
        description=f"Created user '{body.username}' with role {body.role}",
        after={"username": body.username, "role": body.role, "dept_id": body.dept_id},
    )
    return {"ok": True}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, user: dict = Depends(require_admin)):
    """
    The frontend always submits the whole edit form at once (role + dept +
    active together, same as the old Streamlit form), so dept_id=None here
    unambiguously means "unassign department" rather than "leave unchanged" —
    unlike auth.db.update_user's own Ellipsis sentinel, which exists for
    callers that only want to touch one field at a time.
    """
    before = next((u for u in authdb.list_users() if u["id"] == user_id), None)
    try:
        authdb.update_user(
            user_id,
            role=body.role,
            dept_id=body.dept_id,
            is_active=body.is_active,
        )
        if body.new_password:
            authdb.set_password(user_id, body.new_password)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    after = next((u for u in authdb.list_users() if u["id"] == user_id), None)
    _log_user_update(user["username"], before, after, password_changed=bool(body.new_password))
    return {"ok": True}


def _log_user_update(actor: str, before: Optional[dict], after: Optional[dict], password_changed: bool):
    if not before or not after:
        return
    target = after.get("username", before.get("username"))
    changes = []

    if before.get("role") != after.get("role"):
        authdb.record_audit(
            actor, "ROLE_CHANGED", "user", target_id=target,
            description=f"Changed {target}'s role: {before.get('role')} -> {after.get('role')}",
            before={"role": before.get("role")}, after={"role": after.get("role")},
        )
        changes.append("role")

    if before.get("dept_id") != after.get("dept_id"):
        authdb.record_audit(
            actor, "DEPARTMENT_CHANGED", "user", target_id=target,
            description=(
                f"Changed {target}'s department: "
                f"{before.get('dept_name') or 'none'} -> {after.get('dept_name') or 'none'}"
            ),
            before={"dept_id": before.get("dept_id"), "dept_name": before.get("dept_name")},
            after={"dept_id": after.get("dept_id"), "dept_name": after.get("dept_name")},
        )
        changes.append("department")

    if before.get("is_active") != after.get("is_active"):
        action = "USER_ACTIVATED" if after.get("is_active") else "USER_DEACTIVATED"
        authdb.record_audit(
            actor, action, "user", target_id=target,
            description=f"{'Activated' if after.get('is_active') else 'Deactivated'} user {target}",
            before={"is_active": before.get("is_active")}, after={"is_active": after.get("is_active")},
        )
        changes.append("status")

    if password_changed:
        authdb.record_audit(
            actor, "USER_UPDATED", "user", target_id=target,
            description=f"Reset password for user {target}",
        )
        changes.append("password")

    if not changes:
        # Save was pressed with no actual field changes — still a
        # deliberate admin action worth one line in the log.
        authdb.record_audit(
            actor, "USER_UPDATED", "user", target_id=target,
            description=f"Updated user {target} (no field changes)",
        )


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(require_admin)):
    before = next((u for u in authdb.list_users() if u["id"] == user_id), None)
    try:
        authdb.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    authdb.record_audit(
        user["username"], "USER_DELETED", "user",
        target_id=before.get("username") if before else user_id,
        description=f"Deleted user '{before.get('username') if before else user_id}'",
        before=before,
    )
    return {"ok": True}


# ── Files ────────────────────────────────────────────────────────────────────
@router.get("/files")
def list_files(
    q: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    department_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: Optional[str] = "newest",
    limit: Optional[int] = None,
):
    """
    Server-side filtered/sorted file listing for the admin panel (Feature
    9). All params are optional; calling with none of them still returns
    every file (now sorted newest-first by default instead of the old
    alphabetical-by-source order, which is a more useful default for an
    admin scanning recent activity — files can still be sorted back to
    "oldest" or searched by name).
    """
    return authdb.list_files(
        q=q, uploaded_by=uploaded_by, dept_id=department_id,
        date_from=date_from, date_to=date_to, sort=sort, limit=limit,
    )


def _get_file_or_404(doc_id: str) -> dict:
    row = next((f for f in authdb.list_files() if f["doc_id"] == doc_id), None)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


@router.patch("/files/{doc_id}/departments")
def set_file_departments(doc_id: str, body: FileDeptUpdate, user: dict = Depends(require_admin)):
    before = _get_file_or_404(doc_id)
    try:
        authdb.set_file_departments(doc_id, body.dept_ids)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    before_names = sorted(d["name"] for d in before.get("departments", []))
    after_row = _get_file_or_404(doc_id)
    after_names = sorted(d["name"] for d in after_row.get("departments", []))
    if before_names != after_names:
        authdb.record_audit(
            user["username"], "FILE_DEPARTMENTS_CHANGED", "file", target_id=doc_id,
            description=f"Changed departments for '{before['source']}': "
                         f"{', '.join(before_names) or 'none'} -> {', '.join(after_names) or 'none'}",
            before={"departments": before_names}, after={"departments": after_names},
        )
    return {"ok": True}


@router.patch("/files/{doc_id}/flags")
def set_file_flags(doc_id: str, body: FileFlagsUpdate, user: dict = Depends(require_admin)):
    before = _get_file_or_404(doc_id)
    authdb.set_file_flags(doc_id, is_public=body.is_public, hidden_by_admin=body.hidden_by_admin)
    after = _get_file_or_404(doc_id)
    _log_file_flag_changes(user["username"], before, after)
    return {"ok": True}


def _log_file_flag_changes(actor: str, before: dict, after: dict):
    source = before["source"]

    if bool(before["hidden_by_admin"]) != bool(after["hidden_by_admin"]):
        action = "FILE_HIDDEN" if after["hidden_by_admin"] else "FILE_UNHIDDEN"
        authdb.record_audit(
            actor, action, "file", target_id=before["doc_id"],
            description=f"{'Hid' if after['hidden_by_admin'] else 'Unhid'} file '{source}'",
            before={"hidden_by_admin": bool(before["hidden_by_admin"])},
            after={"hidden_by_admin": bool(after["hidden_by_admin"])},
        )

    if bool(before["is_public"]) != bool(after["is_public"]):
        authdb.record_audit(
            actor, "FILE_VISIBILITY_CHANGED", "file", target_id=before["doc_id"],
            description=(
                f"Changed '{source}' visibility: "
                f"{'public' if before['is_public'] else 'restricted'} -> "
                f"{'public' if after['is_public'] else 'restricted'}"
            ),
            before={"is_public": bool(before["is_public"])},
            after={"is_public": bool(after["is_public"])},
        )


@router.delete("/files/{doc_id}")
def delete_file_permanently(doc_id: str, user: dict = Depends(require_admin)):
    """
    Option A from Feature 4 — irreversible. Wipes ChromaDB chunks, the
    auth.db row, and the physical file (see
    api.services.delete_file_completely for the exact ordering/safety
    reasoning). Distinct from PATCH .../flags{hidden_by_admin:true}, which
    is Option B and touches only the auth.db visibility flag.
    """
    from api.services import delete_file_completely

    before = _get_file_or_404(doc_id)
    result = delete_file_completely(doc_id)

    authdb.record_audit(
        user["username"], "FILE_DELETED", "file", target_id=doc_id,
        description=f"Permanently deleted file '{before['source']}'"
                     + (f" (with warnings: {'; '.join(result['warnings'])})" if result["warnings"] else ""),
        before={"source": before["source"], "uploaded_by": before.get("uploaded_by")},
    )
    return result


@router.post("/files/bulk")
def bulk_file_action(body: BulkFileActionRequest, user: dict = Depends(require_admin)):
    """
    Feature 5 — every doc_id is independently re-validated against the
    current file list server-side (never trusting that the frontend's
    checkbox selection is still accurate/authorized by the time this
    request arrives). One bad id in the batch is reported per-item and
    does not abort or silently skip the rest.
    """
    from api.services import delete_file_completely

    current = {f["doc_id"]: f for f in authdb.list_files()}
    results = []

    for doc_id in body.doc_ids:
        row = current.get(doc_id)
        if not row:
            results.append({"doc_id": doc_id, "ok": False, "error": "File not found"})
            continue
        try:
            if body.action == "delete":
                outcome = delete_file_completely(doc_id)
                authdb.record_audit(
                    user["username"], "FILE_DELETED", "file", target_id=doc_id,
                    description=f"Permanently deleted file '{row['source']}' (bulk action)",
                    before={"source": row["source"], "uploaded_by": row.get("uploaded_by")},
                )
                results.append({"doc_id": doc_id, "ok": True, "warnings": outcome["warnings"]})
            elif body.action in ("hide", "unhide"):
                hidden = body.action == "hide"
                authdb.set_file_flags(doc_id, hidden_by_admin=hidden)
                authdb.record_audit(
                    user["username"], "FILE_HIDDEN" if hidden else "FILE_UNHIDDEN",
                    "file", target_id=doc_id,
                    description=f"{'Hid' if hidden else 'Unhid'} file '{row['source']}' (bulk action)",
                    before={"hidden_by_admin": bool(row["hidden_by_admin"])},
                    after={"hidden_by_admin": hidden},
                )
                results.append({"doc_id": doc_id, "ok": True})
        except Exception as e:
            results.append({"doc_id": doc_id, "ok": False, "error": str(e)})

    return {"results": results}


# ── Audit log ────────────────────────────────────────────────────────────────
@router.get("/audit-log")
def get_audit_log(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
):
    return authdb.list_audit_log(
        limit=limit, actor_username=actor, action=action, date_from=date_from, date_to=date_to,
    )


@router.get("/audit-log/actions")
def get_audit_log_actions():
    return authdb.list_audit_actions()
