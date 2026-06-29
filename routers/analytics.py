from __future__ import annotations
"""
Analytics routes.

GET /analytics/stats    — profile KPI stats (views, swipe-ups, subscribers, etc.)
GET /analytics/content  — list of published posts with per-post metrics
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
import snap_client
from auth_utils import get_current_user
from database import get_db
from models import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/stats")
def get_stats(
    profile_id: str = Query(...),
    start_time: str = Query(..., description="ISO 8601 datetime"),
    end_time: str = Query(..., description="ISO 8601 datetime"),
    granularity: str = Query("DAY", description="DAY | WEEK | MONTH | LIFETIME"),
    fields: str = Query(
        None,
        description="Comma-separated Snap API field names. Defaults to all key metrics.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not fields:
        fields = (
            "story_impressions,subscriber_count,avg_screen_time_millis,"
            "swipe_up_count,share_count,reply_count,total_time_viewed"
        )
    data = snap_client.get_profile_stats(user, db, profile_id, start_time, end_time, granularity, fields)

    # Normalise the response so the dashboard always has a consistent shape
    # Snap returns stats nested under different keys depending on the endpoint version
    metrics = data.get("total_stats") or data.get("stats") or data.get("metrics") or {}
    timeseries = data.get("timeseries") or data.get("daily_stats") or []

    return {
        "metrics": metrics,
        "timeseries": timeseries,
        "raw": data,  # pass through full Snap response for debugging
    }


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
