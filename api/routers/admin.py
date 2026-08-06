from fastapi import APIRouter, Depends, HTTPException, status

from auth import db as authdb
from api.deps import require_admin
from api.schemas import (
    DepartmentCreate, DepartmentRename,
    UserCreate, UserUpdate,
    FileDeptUpdate, FileFlagsUpdate,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ── Departments ──────────────────────────────────────────────────────────────
@router.get("/departments")
def list_departments():
    return authdb.list_departments()


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def add_department(body: DepartmentCreate):
    try:
        authdb.add_department(body.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.patch("/departments/{dept_id}")
def rename_department(dept_id: int, body: DepartmentRename):
    try:
        authdb.rename_department(dept_id, body.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: int):
    authdb.delete_department(dept_id)
    return {"ok": True}


# ── Users ────────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users():
    return authdb.list_users()


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate):
    try:
        authdb.create_user(body.username, body.password, role=body.role, dept_id=body.dept_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate):
    """
    The frontend always submits the whole edit form at once (role + dept +
    active together, same as the old Streamlit form), so dept_id=None here
    unambiguously means "unassign department" rather than "leave unchanged" —
    unlike auth.db.update_user's own Ellipsis sentinel, which exists for
    callers that only want to touch one field at a time.
    """
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
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int):
    try:
        authdb.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


# ── Files ────────────────────────────────────────────────────────────────────
@router.get("/files")
def list_files():
    return authdb.list_files()


@router.patch("/files/{doc_id}/departments")
def set_file_departments(doc_id: str, body: FileDeptUpdate):
    try:
        authdb.set_file_departments(doc_id, body.dept_ids)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.patch("/files/{doc_id}/flags")
def set_file_flags(doc_id: str, body: FileFlagsUpdate):
    authdb.set_file_flags(doc_id, is_public=body.is_public, hidden_by_admin=body.hidden_by_admin)
    return {"ok": True}
