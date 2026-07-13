from __future__ import annotations
"""
GET /profiles — list all Snapchat Public Profiles for the authenticated user.
"""
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import snap_client
from auth_utils import get_current_user
from database import get_db
from models import User

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("")
def list_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profiles = snap_client.get_user_profiles(user, db)
    return profiles


@router.get("/debug")
def debug_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Probe the real stats endpoint for the user's own profile and return the
    raw Snap response so we can see the exact metric names + shape. No token
    is exposed in the output."""
    from datetime import datetime, timezone, timedelta
    token = snap_client.ensure_fresh_token(user, db)
    headers = {"Authorization": f"Bearer {token}"}
    B = "https://businessapi.snapchat.com/v1"
    out: dict = {"token_len": len(token or "")}

    # 1) my_profile → get the profile id
    prof_id = None
    try:
        r = httpx.get(f"{B}/public_profiles/my_profile", headers=headers, timeout=20)
        j = r.json()
        prof_id = (j.get("public_profile") or j.get("me") or j).get("id")
        out["my_profile"] = {"status": r.status_code, "id": prof_id,
                             "display_name": (j.get("public_profile") or {}).get("display_name")}
    except Exception as e:
        out["my_profile_error"] = str(e)

    if not prof_id:
        return out

    # 2) stats — try camelCase params + UPPERCASE fields (per Snap docs)
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    fields = "IMPRESSIONS,STORY_VIEWS,VIEW_TIME,SUBSCRIBERS,SUBSCRIBE_COUNT,SHARES,REPLIES,SCREENSHOTS,SWIPE_UPS"
    stats_variants = [
        ("camelCase+assetType", {"granularity": "DAY", "startTime": start.strftime(fmt),
                                 "endTime": end.strftime(fmt), "fields": fields, "assetType": "PROFILE"}),
        ("camelCase+LIFETIME",  {"granularity": "LIFETIME", "fields": fields, "assetType": "PROFILE"}),
        ("snake_case (current)", {"granularity": "DAY", "start_time": start.strftime(fmt),
                                  "end_time": end.strftime(fmt), "fields": fields.lower()}),
    ]
    out["stats"] = []
    for label, params in stats_variants:
        entry = {"variant": label, "params": params}
        try:
            r = httpx.get(f"{B}/public_profiles/{prof_id}/stats", headers=headers, params=params, timeout=25)
            entry["status"] = r.status_code
            entry["body"] = r.text[:1500]
        except Exception as e:
            entry["exception"] = str(e)
        out["stats"].append(entry)
    return out
