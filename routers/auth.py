from __future__ import annotations
"""
Authentication routes — Snapchat OAuth 2.0 flow.

GET  /auth/login     → redirect to Snapchat OAuth
GET  /auth/callback  → exchange code, set session cookie
POST /auth/logout    → clear session
GET  /auth/me        → return current user info
"""
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import snap_client
from auth_utils import create_jwt, get_current_user
from config import settings
from database import get_db
from models import User

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory state store — replace with Redis in production
_oauth_states: dict[str, str] = {}


@router.get("/login")
def login():
    """Redirect the browser to Snapchat's OAuth authorization page."""
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = state   # store for CSRF validation
    return RedirectResponse(snap_client.build_auth_url(state))


@router.get("/callback")
def callback(code: str, state: str, db: Session = Depends(get_db)):
    """
    Snapchat redirects here after the user approves access.
    Exchange the code for tokens, upsert the user, set a JWT cookie.
    """
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    del _oauth_states[state]

    token_data = snap_client.exchange_code(code)
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 3600)
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Fetch user identity from Snap
    snap_user = snap_client.snap_get_raw(access_token, "me")
    snap_user_id = snap_user.get("me", {}).get("id") or snap_user.get("sub") or snap_user.get("id")
    display_name = snap_user.get("me", {}).get("display_name") or snap_user.get("display_name")
    email = snap_user.get("me", {}).get("email") or snap_user.get("email")

    # Upsert user
    user = db.query(User).filter(User.snap_user_id == snap_user_id).first()
    if user:
        user.access_token = access_token
        user.refresh_token = refresh_token
        user.token_expires_at = token_expires_at
        user.display_name = display_name
        user.email = email
    else:
        user = User(
            snap_user_id=snap_user_id,
            display_name=display_name,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    # Set JWT session cookie and redirect to frontend dashboard
    response = RedirectResponse(url=f"{settings.FRONTEND_URL}/dashboard")
    response.set_cookie(
        key="session",
        value=create_jwt(user.id),
        httponly=True,
        secure=settings.REDIRECT_URI.startswith("https"),
        samesite="lax",
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
    )
    return response


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "snap_user_id": user.snap_user_id,
        "display_name": user.display_name,
        "email": user.email,
    }
