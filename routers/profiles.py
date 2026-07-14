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


@router.get("/test")
def test_access(ids: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Given ?ids=a,b,c (Public Profile UUIDs OR usernames), test whether this
    token can read each profile's metadata AND its stats. No token exposed."""
    from datetime import datetime, timezone, timedelta
    token = snap_client.ensure_fresh_token(user, db)
    headers = {"Authorization": f"Bearer {token}"}
    B = "https://businessapi.snapchat.com/v1"
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    values = [v.strip() for v in ids.split(",") if v.strip()]
    out: dict = {"tested": len(values), "results": []}
    for v in values:
        entry = {"input": v}
        # metadata lookup (public read)
        try:
            r = httpx.get(f"{B}/public_profiles/{v}", headers=headers, timeout=20)
            entry["lookup_status"] = r.status_code
            try:
                pp = (r.json().get("public_profile") or {})
                entry["name"] = pp.get("display_name")
                entry["resolved_id"] = pp.get("id")
            except Exception:
                entry["lookup_body"] = r.text[:300]
        except Exception as e:
            entry["lookup_error"] = str(e)
        # stats access (permissioned)
        pid = entry.get("resolved_id") or v
        try:
            r = httpx.get(f"{B}/public_profiles/{pid}/stats", headers=headers,
                          params={"granularity": "TOTAL", "startTime": start.strftime(fmt),
                                  "endTime": end.strftime(fmt), "fields": snap_client.PROFILE_FIELDS,
                                  "assetType": "PROFILE"}, timeout=25)
            entry["stats_status"] = r.status_code
            entry["stats_access"] = "YES ✅" if r.status_code == 200 else "NO ❌"
            if r.status_code != 200:
                entry["stats_body"] = r.text[:300]
        except Exception as e:
            entry["stats_error"] = str(e)
        out["results"].append(entry)
    return out


@router.get("/debug")
def debug_org_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Probe org-scoped endpoints to discover every profile the user can access.
    No token exposed."""
    token = snap_client.ensure_fresh_token(user, db)
    headers = {"Authorization": f"Bearer {token}"}
    B = "https://businessapi.snapchat.com/v1"
    out: dict = {}

    # 1) my_profile → org id
    org_ids: list[str] = []
    try:
        r = httpx.get(f"{B}/public_profiles/my_profile", headers=headers, timeout=20)
        j = r.json()
        pp = j.get("public_profile") or {}
        out["my_profile"] = {"status": r.status_code, "id": pp.get("id"),
                             "org_id": pp.get("organization_id"), "name": pp.get("display_name")}
        if pp.get("organization_id"):
            org_ids.append(pp["organization_id"])
    except Exception as e:
        out["my_profile_error"] = str(e)

    # 2) try to discover ALL organizations the user belongs to
    out["org_discovery"] = []
    for path in ("me/organizations", "me", "organizations"):
        try:
            r = httpx.get(f"{B}/{path}", headers=headers, timeout=20)
            entry = {"path": path, "status": r.status_code, "body": r.text[:600]}
            try:
                j = r.json()
                orgs = j.get("organizations") or []
                for o in orgs:
                    oo = o.get("organization", o)
                    if oo.get("id") and oo["id"] not in org_ids:
                        org_ids.append(oo["id"])
            except Exception:
                pass
            out["org_discovery"].append(entry)
        except Exception as e:
            out["org_discovery"].append({"path": path, "exception": str(e)})

    # 3) for each known org, list its public profiles
    out["org_profiles"] = []
    for oid in org_ids:
        try:
            r = httpx.get(f"{B}/organizations/{oid}/public_profiles",
                          headers=headers, params={"limit": 50}, timeout=25)
            entry = {"org_id": oid, "status": r.status_code}
            try:
                j = r.json()
                pps = j.get("public_profiles") or []
                entry["count"] = len(pps)
                entry["profiles"] = [
                    {"id": (p.get("public_profile") or p).get("id"),
                     "name": (p.get("public_profile") or p).get("display_name")}
                    for p in pps
                ]
            except Exception:
                entry["body"] = r.text[:600]
            out["org_profiles"].append(entry)
        except Exception as e:
            out["org_profiles"].append({"org_id": oid, "exception": str(e)})

    return out
