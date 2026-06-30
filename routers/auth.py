from __future__ import annotations
"""
Authentication routes — Snapchat OAuth 2.0 flow.

GET  /auth/login     → redirect to Snapchat OAuth
GET  /auth/callback  → exchange code, set session cookie
POST /auth/logout    → clear session
GET  /auth/me        → return current user info
"""
import base64
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
import httpx
from sqlalchemy.orm import Session
import snap_client
from auth_utils import create_jwt, get_current_user
from config import settings
from database import get_db
from models import User

logger = logging.getLogger(__name__)

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

    try:
        token_data = snap_client.exchange_code(code)
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Fetch user identity — try multiple Snap endpoints, fall back to JWT decode
    snap_user_id = None
    display_name = None
    email = None

    me_candidates = [
        ("businessapi.snapchat.com/v1", "me"),
        ("adsapi.snapchat.com/v1", "me"),
        ("accounts.snapchat.com/accounts", "userinfo"),
    ]
    for base, path in me_candidates:
        try:
            resp = httpx.get(
                f"https://{base}/{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                snap_user = resp.json()
                logger.info("Got user info from %s/%s: %s", base, path, list(snap_user.keys()))
                snap_user_id = (snap_user.get("me", {}).get("id")
                                or snap_user.get("sub")
                                or snap_user.get("id"))
                display_name = (snap_user.get("me", {}).get("display_name")
                                or snap_user.get("display_name")
                                or snap_user.get("name"))
                email = (snap_user.get("me", {}).get("email")
                         or snap_user.get("email"))
                break
            else:
                logger.warning("GET %s/%s returned %s: %s", base, path, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("GET %s/%s error: %s", base, path, exc)

    # Last resort: decode the JWT access token payload for the sub claim
    if not snap_user_id:
        try:
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            logger.info("JWT payload keys: %s", list(payload.keys()))
            snap_user_id = payload.get("sub") or payload.get("id") or payload.get("user_id")
        except Exception as exc:
            logger.warning("JWT decode fallback failed: %s", exc)

    if not snap_user_id:
        raise HTTPException(status_code=502, detail="Could not determine Snap user ID from token or /me endpoints")

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
