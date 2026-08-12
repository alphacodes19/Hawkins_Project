from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ── Auth ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    dept_id: Optional[int] = None
    dept_name: Optional[str] = None
    is_active: bool = True
    has_avatar: bool = False

    class Config:
        extra = "ignore"

    @model_validator(mode="before")
    @classmethod
    def _derive_has_avatar(cls, data):
        """
        Callers (login/me/avatar endpoints) pass through the raw user dict
        from auth.db, which carries `avatar_path` (a server filesystem
        path) — never something we want in the JSON response. This derives
        the public `has_avatar` boolean from it.
        """
        if isinstance(data, dict) and "has_avatar" not in data:
            data = {**data, "has_avatar": bool(data.get("avatar_path"))}
        return data


# ── Search / chat ────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    top_n_docs: int = 20


class ChatRequest(BaseModel):
    question: str
    # The doc list from a prior /api/search call — reused so the LLM answer
    # is grounded in exactly what the user is looking at, and so we don't
    # re-run retrieval twice for one query (mirrors app.py's stream_answer(docs=...)).
    docs: list = Field(default_factory=list)


# ── Admin: departments ───────────────────────────────────────────────────────
class DepartmentCreate(BaseModel):
    name: str


class DepartmentRename(BaseModel):
    name: str


# ── Admin: users ─────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "viewer"
    dept_id: Optional[int] = None


class UserUpdate(BaseModel):
    role: Optional[str] = None
    dept_id: Optional[int] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None


# ── Admin: files ─────────────────────────────────────────────────────────────
class FileDeptUpdate(BaseModel):
    dept_ids: list[int]


class FileFlagsUpdate(BaseModel):
    is_public: Optional[bool] = None
    hidden_by_admin: Optional[bool] = None


class BulkFileActionRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["delete", "hide", "unhide"]


# ── Upload ────────────────────────────────────────────────────────────────────
class UploadACL(BaseModel):
    dept_ids: list[int] = Field(default_factory=list)
    is_public: bool = False
