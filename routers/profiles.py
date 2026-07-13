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
    """Probe candidate Snap API endpoints to find which one returns the user's
    organizations / public profiles. No token is exposed in the output."""
    token = snap_client.ensure_fresh_token(user, db)
    # /v1/ surface wants only Authorization on GET
    headers = {"Authorization": f"Bearer {token}"}
    B = "https://businessapi.snapchat.com/v1"
    candidates = [
        ("v1/public_profiles/my_profile", f"{B}/public_profiles/my_profile"),
    ]
    out: dict = {"token_len": len(token or ""), "results": []}
    for label, url in candidates:
        entry = {"endpoint": label, "url": url}
        try:
            r = httpx.get(url, headers=headers, timeout=20)
            entry["status"] = r.status_code
            body = r.text[:800]
            entry["body"] = body
            # Surface any organization_id we can spot
            try:
                j = r.json()
                orgs = j.get("organizations")
                if isinstance(orgs, list) and orgs:
                    ids = []
                    for o in orgs:
                        oo = o.get("organization", o)
                        if oo.get("id"):
                            ids.append({"id": oo["id"], "name": oo.get("name")})
                    entry["found_org_ids"] = ids
            except Exception:
                pass
        except Exception as e:
            entry["exception"] = str(e)
        out["results"].append(entry)
    return out
