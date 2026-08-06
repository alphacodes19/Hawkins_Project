"""
api/main.py — FastAPI entrypoint
====================================
Run with:
    uvicorn api.main:app --reload --port 8000

Run from the project root (same requirement app.py had for config.py's
relative paths to resolve) — see the CRITICAL comment this mirrors from
app.py's original sys.path/os.chdir block.
"""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auth import db as authdb  # noqa: E402
from api.services import warmup_search_stack  # noqa: E402
from api.routers import auth, search, chat, admin, upload, files  # noqa: E402
from api.deps import get_current_user  # noqa: E402

# Comma-separated list of allowed frontend origins, e.g.
#   HAWKINS_CORS_ORIGINS="http://localhost:3000,https://archive.hawkins.internal"
_origins = os.environ.get("HAWKINS_CORS_ORIGINS", "http://localhost:3000").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    authdb.init_db()
    warmup_search_stack()
    yield


app = FastAPI(title="Hawkins Data Archive API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,   # required so the browser sends the session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(upload.router)
app.include_router(files.router)


@app.get("/api/departments")
def list_departments_public(user: dict = Depends(get_current_user)):
    """
    Read-only department list for any signed-in user — the upload dialog
    needs this for uploaders, who aren't admins and can't reach
    /api/admin/departments (that one's the create/rename/delete surface).
    """
    return authdb.list_departments()


@app.get("/api/health")
def health():
    return {"status": "ok"}
