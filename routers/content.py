from __future__ import annotations
"""
Content & scheduling routes.

POST   /content/upload           — encrypt + upload media, return media_id
POST   /content/story            — publish a Story immediately
POST   /content/spotlight        — publish a Spotlight immediately
POST   /content/saved-story      — publish a Saved Story immediately
POST   /content/schedule         — queue a post for later
GET    /content/scheduled        — list scheduled posts
DELETE /content/scheduled/{id}   — cancel a scheduled post
"""
import logging
import httpx
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.orm import Session
import snap_client
import encryption as enc
from auth_utils import get_current_user
from database import get_db
from models import ScheduledPost, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["content"])


# ── MEDIA UPLOAD ──

@router.post("/upload")
async def upload_media(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Accepts raw media from the browser, encrypts it AES-256-CBC server-side,
    uploads the encrypted bytes to Snap, and returns the media_id.
    """
    raw = await file.read()
    encrypted_bytes, key_b64, iv_b64 = enc.encrypt_media(raw)
    mime = file.content_type or "video/mp4"

    try:
        media_id = snap_client.upload_media(user, db, profile_id, encrypted_bytes, key_b64, iv_b64, mime)
    except httpx.HTTPStatusError as exc:
        # Surface the real Snap error (with a clean, CORS-friendly response)
        body = exc.response.text[:400]
        logger.error("Snap media upload failed (%s): %s", exc.response.status_code, body)
        detail = f"Snapchat rejected the upload ({exc.response.status_code}). "
        if exc.response.status_code == 403:
            detail += "This account may not have posting permission."
        else:
            detail += body
        raise HTTPException(status_code=502, detail=detail)
    except Exception as exc:
        logger.exception("Media upload error")
        raise HTTPException(status_code=502, detail=f"Upload failed: {exc}")

    return {"media_id": media_id, "filename": file.filename, "size_bytes": len(raw)}


# ── IMMEDIATE PUBLISH ──

class StoryRequest(BaseModel):
    profile_id: str
    media_id: str
    caption: Optional[str] = None


class SpotlightRequest(BaseModel):
    profile_id: str
    media_id: str
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None


@router.post("/story")
def post_story(body: StoryRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = snap_client.post_story(user, db, body.profile_id, body.media_id, body.caption)
    return result


@router.post("/spotlight")
def post_spotlight(body: SpotlightRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = snap_client.post_spotlight(user, db, body.profile_id, body.media_id, body.caption, body.hashtags)
    return result


@router.post("/saved-story")
def post_saved_story(body: StoryRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = snap_client.post_saved_story(user, db, body.profile_id, body.media_id, body.caption)
    return result


# ── SCHEDULING ──

class ScheduleRequest(BaseModel):
    profile_id: str
    post_type: str             # story | spotlight | saved_story
    media_id: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None
    publish_at: datetime       # ISO 8601 with timezone


@router.post("/schedule")
def schedule_post(
    body: ScheduleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.post_type not in ("story", "spotlight", "saved_story"):
        raise HTTPException(400, "post_type must be story, spotlight, or saved_story")
    post = ScheduledPost(
        user_id=user.id,
        profile_id=body.profile_id,
        post_type=body.post_type,
        media_id=body.media_id,
        caption=body.caption,
        hashtags=body.hashtags,
        publish_at=body.publish_at,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _serialise_post(post)


@router.get("/scheduled")
def list_scheduled(
    profile_id: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    posts = (
        db.query(ScheduledPost)
        .filter(
            ScheduledPost.user_id == user.id,
            ScheduledPost.profile_id == profile_id,
            ScheduledPost.status == "scheduled",
        )
        .order_by(ScheduledPost.publish_at)
        .all()
    )
    return {"items": [_serialise_post(p) for p in posts]}


@router.delete("/scheduled/{post_id}")
def cancel_scheduled(
    post_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = db.query(ScheduledPost).filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user.id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.status != "scheduled":
        raise HTTPException(400, f"Cannot cancel a post with status '{post.status}'")
    post.status = "cancelled"
    db.commit()
    return {"ok": True}


def _serialise_post(p: ScheduledPost) -> dict:
    return {
        "id": p.id,
        "profile_id": p.profile_id,
        "post_type": p.post_type,
        "media_id": p.media_id,
        "caption": p.caption,
        "hashtags": p.hashtags,
        "publish_at": p.publish_at.isoformat(),
        "status": p.status,
        "error_message": p.error_message,
        "created_at": p.created_at.isoformat(),
    }
