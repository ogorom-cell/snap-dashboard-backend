from __future__ import annotations
"""
Snapchat API client.
Wraps all calls to businessapi.snapchat.com and handles token refresh transparently.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from sqlalchemy.orm import Session
from config import settings
from models import User

SNAP_API_BASE = "https://businessapi.snapchat.com/public/v1/public_profiles"
TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"


def build_auth_url(state: str) -> str:
    params = {
        "client_id": settings.SNAP_CLIENT_ID,
        "redirect_uri": settings.REDIRECT_URI,
        "response_type": "code",
        "scope": "snapchat-profile-api",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Exchange OAuth code for access + refresh tokens."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.SNAP_CLIENT_ID,
            "client_secret": settings.SNAP_CLIENT_SECRET,
            "redirect_uri": settings.REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    return resp.json()


def refresh_tokens(refresh_token: str) -> dict:
    """Get a new access token using the refresh token."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.SNAP_CLIENT_ID,
            "client_secret": settings.SNAP_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    return resp.json()


def ensure_fresh_token(user: User, db: Session) -> str:
    """Return a valid access token, refreshing if it expires within 5 minutes."""
    buffer = timedelta(minutes=5)
    now = datetime.now(timezone.utc)
    expires_at = user.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now + buffer >= expires_at:
        data = refresh_tokens(user.refresh_token)
        user.access_token = data["access_token"]
        user.refresh_token = data.get("refresh_token", user.refresh_token)
        user.token_expires_at = now + timedelta(seconds=data.get("expires_in", 3600))
        db.commit()

    return user.access_token


def snap_get(user: User, db: Session, path: str, params: dict = None) -> dict:
    """Authenticated GET to the Snap Business API (/v1/ surface, Authorization only)."""
    token = ensure_fresh_token(user, db)
    resp = httpx.get(
        f"https://businessapi.snapchat.com/v1/{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def snap_post(user: User, db: Session, path: str, json: dict = None, data=None, files=None) -> dict:
    """Authenticated POST to the Snap Business API.

    Do NOT force Content-Type here — httpx sets application/json for json=,
    and the correct multipart boundary for files=. Forcing it breaks uploads.
    """
    token = ensure_fresh_token(user, db)
    resp = httpx.post(
        f"https://businessapi.snapchat.com/v1/{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=json,
        data=data,
        files=files,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def snap_get_raw(access_token: str, path: str, params: dict = None) -> dict:
    """Authenticated GET using a raw access token."""
    resp = httpx.get(
        f"https://businessapi.snapchat.com/v1/{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize_profile(p: dict) -> dict:
    """Map a Snap public_profile object to the shape the frontend expects."""
    logos = p.get("logo_urls") or {}
    return {
        "id": p.get("id"),
        "name": p.get("display_name") or p.get("name") or "My Profile",
        # Snap public profiles have no @username; leave null rather than showing the UUID
        "username": p.get("username") or p.get("handle") or None,
        "logo_url": (p.get("profile_picture_uri") or p.get("logo_url")
                     or logos.get("discover_feed_logo_url") or logos.get("original_logo_url")),
        "raw": p,
    }


def get_user_profiles(user: User, db: Session) -> list[dict]:
    """Return the authenticated user's own public profile(s).

    Snap's magic endpoint /v1/public_profiles/my_profile returns the profile
    tied to the user token (snapchat-profile-api scope). Response is wrapped;
    we defensively unwrap the common shapes.
    """
    data = snap_get(user, db, "public_profiles/my_profile")

    # Shape A: {"me": {...}} or {"public_profile": {...}}
    for key in ("me", "public_profile"):
        obj = data.get(key)
        if isinstance(obj, dict) and obj.get("id"):
            return [_normalize_profile(obj)]

    # Shape B: {"public_profiles": [{"public_profile": {...}}, ...]}
    lst = data.get("public_profiles")
    if isinstance(lst, list) and lst:
        out = []
        for item in lst:
            obj = item.get("public_profile", item) if isinstance(item, dict) else {}
            if obj.get("id"):
                out.append(_normalize_profile(obj))
        if out:
            return out

    # Shape C: profile fields at top level
    if data.get("id"):
        return [_normalize_profile(data)]

    return []


# Valid PROFILE metric enum names (Snap Public Profile Metrics API)
PROFILE_FIELDS = "SUBSCRIBERS,SUBSCRIBERS_GAINED,STORY_VIEWS,AVG_VIEW_TIME_MILLIS,SHARES,VIEWERS,VIEWS,INTERACTIONS"


def get_profile_stats(user: User, db: Session, profile_id: str, start_time: str, end_time: str, granularity: str = "DAY", fields: str = None) -> dict:
    """Call the Snap stats endpoint. Uses camelCase startTime/endTime + assetType
    (the shape Snap actually validates). start_time/end_time must be
    yyyy-mm-ddT00:00:00.000Z."""
    params = {
        "granularity": granularity,
        "fields": fields or PROFILE_FIELDS,
        "assetType": "PROFILE",
    }
    if granularity != "LIFETIME":
        params["startTime"] = start_time
        params["endTime"] = end_time
    return snap_get(user, db, f"public_profiles/{profile_id}/stats", params)


def _stat_value_from_list(stats: list) -> float:
    """Pull the DEFAULT-breakdown numeric value out of a stats list."""
    for s in (stats or []):
        if s.get("dimension_breakdown") in (None, "DEFAULT"):
            try:
                return float(s.get("value") or 0)
            except (TypeError, ValueError):
                return 0.0
    # No DEFAULT row — fall back to the first value present
    if stats:
        try:
            return float(stats[0].get("value") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def parse_stats(data: dict) -> tuple[dict, list]:
    """Parse Snap's assets[].timeseries[].fields[] into
    (totals dict {FIELD_NAME: number}, timeseries list of {start_time, <fields>}).

    Each entry in fields[] looks like:
        {"field": {"field_name": "VIEWS"}, "stats": [{"dimension_breakdown": "DEFAULT", "value": "123"}]}
    Note: `stats` is a SIBLING of `field`, not nested inside it.
    """
    assets = data.get("assets") or []
    if not assets:
        return {}, []
    timeseries = assets[0].get("timeseries") or []
    series: list = []
    totals: dict = {}
    for bucket in timeseries:
        row = {"start_time": bucket.get("start_time"), "end_time": bucket.get("end_time")}
        for f in bucket.get("fields") or []:
            fld = f.get("field") or {}
            name = fld.get("field_name") or f.get("field_name")
            if not name:
                continue
            # stats live at the entry level (sibling of "field"); fall back to inside field
            stats = f.get("stats") or fld.get("stats") or []
            val = _stat_value_from_list(stats)
            row[name] = val
            totals[name] = totals.get(name, 0) + val
        series.append(row)
    return totals, series


def search_profiles(user: User, db: Session, query: str) -> list[dict]:
    """Resolve a username/display name to public profiles via Creator Discovery search."""
    token = ensure_fresh_token(user, db)
    resp = httpx.get(
        "https://businessapi.snapchat.com/public/v1/public_profiles/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": query, "limit": 10},
        timeout=20,
    )
    if resp.status_code != 200:
        return []
    out = []
    for item in resp.json().get("public_profiles", []):
        pp = item.get("public_profile") or {}
        if pp.get("id"):
            out.append(pp)
    return out


def has_stats_access(user: User, db: Session, profile_id: str) -> bool:
    """True if this token can read the given profile's stats (i.e. user manages it)."""
    token = ensure_fresh_token(user, db)
    resp = httpx.get(
        f"https://businessapi.snapchat.com/v1/public_profiles/{profile_id}/stats",
        headers={"Authorization": f"Bearer {token}"},
        params={"granularity": "LIFETIME", "fields": "SUBSCRIBERS", "assetType": "PROFILE"},
        timeout=20,
    )
    return resp.status_code == 200


def get_profile_content(user: User, db: Session, profile_id: str, limit: int = 20) -> dict:
    return snap_get(user, db, f"public_profiles/{profile_id}/media", {"limit": limit})


def post_story(user: User, db: Session, profile_id: str, media_id: str, caption: str = None) -> dict:
    payload = {"media_id": media_id}
    if caption:
        payload["caption"] = caption
    return snap_post(user, db, f"public_profiles/{profile_id}/stories", json=payload)


def post_spotlight(user: User, db: Session, profile_id: str, media_id: str, caption: str = None, hashtags: list[str] = None) -> dict:
    payload = {"media_id": media_id}
    if caption:
        payload["description"] = caption
    if hashtags:
        payload["hashtags"] = hashtags
    return snap_post(user, db, f"public_profiles/{profile_id}/spotlights", json=payload)


def post_saved_story(user: User, db: Session, profile_id: str, media_id: str, caption: str = None) -> dict:
    payload = {"media_id": media_id}
    if caption:
        payload["caption"] = caption
    return snap_post(user, db, f"public_profiles/{profile_id}/saved_stories", json=payload)


def upload_media(user: User, db: Session, profile_id: str, encrypted_bytes: bytes, key_b64: str, iv_b64: str, mime_type: str = "video/mp4") -> str:
    """Upload AES-encrypted media to Snap and return the media_id."""
    data = snap_post(
        user, db,
        f"public_profiles/{profile_id}/media",
        files={"file": ("media", encrypted_bytes, mime_type)},
        data={"encryption_key": key_b64, "encryption_iv": iv_b64},
    )
    return data["media"]["id"]
