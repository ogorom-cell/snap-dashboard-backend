from __future__ import annotations
"""
Profiles: the user's own Snap public profile plus any accounts they've added.

GET    /profiles          — own profile + managed accounts
GET    /profiles/search   — search Snap public profiles by username/name
POST   /profiles/add      — add an account (by username or profile_id) if the
                            token has stats access to it
DELETE /profiles/{id}     — remove a managed account
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
import snap_client
from auth_utils import get_current_user
from database import get_db
from models import User, ManagedProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _managed_to_dict(m: ManagedProfile) -> dict:
    return {
        "id": m.profile_id,
        "name": m.display_name or "Profile",
        "username": m.username,
        "logo_url": m.logo_url,
        "managed": True,
        "row_id": m.id,
    }


@router.get("")
def list_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # The user's own profile (from Snap), marked as owner
    own = snap_client.get_user_profiles(user, db)
    for p in own:
        p["owner"] = True
    own_ids = {p["id"] for p in own}

    # Accounts the user has added
    managed = (
        db.query(ManagedProfile)
        .filter(ManagedProfile.user_id == user.id)
        .order_by(ManagedProfile.added_at.asc())
        .all()
    )
    managed_dicts = [_managed_to_dict(m) for m in managed if m.profile_id not in own_ids]
    return own + managed_dicts


@router.get("/search")
def search(q: str = Query(..., min_length=2), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    results = snap_client.search_profiles(user, db, q)
    return [
        {
            "id": p.get("id"),
            "username": p.get("snap_user_name"),
            "name": p.get("display_name"),
            "logo_url": (p.get("logo_urls") or {}).get("discover_feed_logo_url"),
        }
        for p in results
    ]


@router.post("/add")
def add_profile(
    username: str = Query(None),
    profile_id: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not username and not profile_id:
        raise HTTPException(status_code=400, detail="Provide username or profile_id")

    resolved_id = profile_id
    display_name = None
    resolved_username = username
    logo_url = None

    # Resolve a username to a profile via search
    if not resolved_id:
        matches = snap_client.search_profiles(user, db, username)
        exact = next((m for m in matches
                      if (m.get("snap_user_name") or "").lower() == username.lower()), None)
        chosen = exact or (matches[0] if matches else None)
        if not chosen:
            raise HTTPException(status_code=404, detail=f"No Snapchat profile found for '{username}'")
        resolved_id = chosen["id"]
        display_name = chosen.get("display_name")
        resolved_username = chosen.get("snap_user_name") or username
        logo_url = (chosen.get("logo_urls") or {}).get("discover_feed_logo_url")

    # Verify the token can actually read this profile's analytics
    if not snap_client.has_stats_access(user, db, resolved_id):
        raise HTTPException(
            status_code=403,
            detail="Your Snapchat account doesn't have analytics access to that profile.",
        )

    # Upsert
    existing = (
        db.query(ManagedProfile)
        .filter(ManagedProfile.user_id == user.id, ManagedProfile.profile_id == resolved_id)
        .first()
    )
    if existing:
        existing.display_name = display_name or existing.display_name
        existing.username = resolved_username or existing.username
        existing.logo_url = logo_url or existing.logo_url
    else:
        db.add(ManagedProfile(
            user_id=user.id, profile_id=resolved_id,
            username=resolved_username, display_name=display_name, logo_url=logo_url,
        ))
    db.commit()
    return {"id": resolved_id, "name": display_name, "username": resolved_username,
            "logo_url": logo_url, "managed": True}


@router.delete("/{profile_id}")
def remove_profile(profile_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = (
        db.query(ManagedProfile)
        .filter(ManagedProfile.user_id == user.id, ManagedProfile.profile_id == profile_id)
        .first()
    )
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}
