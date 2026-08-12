import os

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.avatars import InvalidAvatarError, delete_avatar_file, save_avatar
from api.deps import COOKIE_NAME, get_current_user
from api.schemas import ChangePasswordRequest, LoginRequest, UserOut
from api.security import create_access_token
from auth import db as authdb

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Set HAWKINS_COOKIE_SECURE=true once you're serving over HTTPS. Off by
# default so local dev over plain http:// isn't silently broken.
COOKIE_SECURE = os.environ.get("HAWKINS_COOKIE_SECURE", "false").lower() == "true"


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response):
    user = authdb.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    token = create_access_token(user["username"], user["role"])
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    return user


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    if not authdb.authenticate(user["username"], body.old_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    authdb.set_password(user["id"], body.new_password)
    return {"ok": True}


# ── Profile photo ────────────────────────────────────────────────────────────
# Deliberately scoped to "your own" everywhere below: every mutating call
# uses the id off the *authenticated session* (`user["id"]`), never a
# client-supplied id, so there is no path by which one signed-in user can
# change another user's photo — matching Feature 1's "only the logged-in
# user may modify their own profile photo" requirement at the backend,
# not just by hiding a button in the UI.
@router.post("/avatar", response_model=UserOut)
async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    raw = await file.read()
    try:
        new_path = save_avatar(user["id"], raw)
    except InvalidAvatarError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    previous = authdb.get_user_by_id(user["id"])
    old_path = previous.get("avatar_path") if previous else None

    authdb.set_user_avatar(user["id"], new_path)
    delete_avatar_file(old_path)  # replace, don't accumulate orphaned files

    return authdb.get_user_by_id(user["id"])


@router.delete("/avatar", response_model=UserOut)
def remove_avatar(user: dict = Depends(get_current_user)):
    current = authdb.get_user_by_id(user["id"])
    delete_avatar_file(current.get("avatar_path") if current else None)
    authdb.set_user_avatar(user["id"], None)
    return authdb.get_user_by_id(user["id"])


@router.get("/avatar/{user_id}")
def get_avatar(user_id: int, user: dict = Depends(get_current_user)):
    """
    Read access is any signed-in user (not just the owner) — avatars are
    low-sensitivity, and the sidebar/admin panel may eventually want to
    show other people's photos too. Only the write endpoints above are
    restricted to "your own account".
    """
    target = authdb.get_user_by_id(user_id)
    path = target.get("avatar_path") if target else None
    if not path or not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo set")
    return FileResponse(path)
