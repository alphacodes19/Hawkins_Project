import os

from fastapi import APIRouter, Depends, HTTPException, Response, status

from auth import db as authdb
from api.deps import COOKIE_NAME, get_current_user
from api.security import create_access_token
from api.schemas import LoginRequest, ChangePasswordRequest, UserOut

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
