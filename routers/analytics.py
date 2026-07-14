from __future__ import annotations
"""
Analytics routes.

GET /analytics/stats    — profile KPI stats (views, swipe-ups, subscribers, etc.)
GET /analytics/content  — list of published posts with per-post metrics
"""
import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import snap_client
from auth_utils import get_current_user
from database import get_db
from models import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/raw")
def raw_stats(profile_id: str = Query(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the raw Snap stats response for a profile so we can see if values
    are genuinely zero or a parsing issue. Temporary diagnostic."""
    from datetime import datetime, timezone, timedelta
    token = snap_client.ensure_fresh_token(user, db)
    headers = {"Authorization": f"Bearer {token}"}
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=30)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    out = {}
    for gran in ("TOTAL", "LIFETIME"):
        params = {"granularity": gran, "fields": snap_client.PROFILE_FIELDS, "assetType": "PROFILE"}
        if gran != "LIFETIME":
            params["startTime"] = start.strftime(fmt)
            params["endTime"] = end.strftime(fmt)
        try:
            r = httpx.get(f"https://businessapi.snapchat.com/v1/public_profiles/{profile_id}/stats",
                          headers=headers, params=params, timeout=25)
            out[gran] = {"status": r.status_code, "body": r.text[:2000]}
        except Exception as e:
            out[gran] = {"exception": str(e)}
    return out


def _snap_time(iso: str) -> str:
    """Normalise any ISO datetime to Snap's required midnight-aligned format."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@router.get("/stats")
def get_stats(
    profile_id: str = Query(...),
    start_time: str = Query(..., description="ISO 8601 datetime"),
    end_time: str = Query(..., description="ISO 8601 datetime"),
    granularity: str = Query("DAY", description="DAY | TOTAL | LIFETIME"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    start = _snap_time(start_time)
    end = _snap_time(end_time)

    # DAY for the time series (chart), TOTAL for the KPI headline numbers
    day_data = snap_client.get_profile_stats(user, db, profile_id, start, end, "DAY")
    total_data = snap_client.get_profile_stats(user, db, profile_id, start, end, "TOTAL")

    totals, _ = snap_client.parse_stats(total_data)
    _, series = snap_client.parse_stats(day_data)

    # Map Snap enum names → stable lowercase keys the dashboard reads
    metrics = {
        "subscribers":          totals.get("SUBSCRIBERS", 0),
        "subscribers_gained":   totals.get("SUBSCRIBERS_GAINED", 0),
        "story_views":          totals.get("STORY_VIEWS", 0),
        "avg_view_time_millis": totals.get("AVG_VIEW_TIME_MILLIS", 0),
        "shares":               totals.get("SHARES", 0),
        "viewers":              totals.get("VIEWERS", 0),
        "views":                totals.get("VIEWS", 0),
        "interactions":         totals.get("INTERACTIONS", 0),
    }
    # Time series for the chart: one point per day with story views
    timeseries = [
        {"start_time": r.get("start_time"), "story_views": r.get("STORY_VIEWS", 0),
         "views": r.get("VIEWS", 0)}
        for r in series
    ]

    return {"metrics": metrics, "timeseries": timeseries}


@router.get("/content")
def get_content(
    profile_id: str = Query(...),
    limit: int = Query(24, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = snap_client.get_profile_content(user, db, profile_id, limit)
    items = data.get("media") or data.get("items") or data.get("content") or []
    return {"items": items, "total": len(items)}
