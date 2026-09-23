"""Read the shared Supabase tier_members roster for the static web build."""

from __future__ import annotations

import os
import sys

import requests

PAGE_SIZE = 1000
TIMEOUT = 30
GENDER_FROM_DB = {"남자": "m", "여자": "f", "m": "m", "f": "f"}
PLACEHOLDER_VALUES = {"체크", "todo", "?", "미정", "", "null", "none", "n/a", "na"}


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in PLACEHOLDER_VALUES:
        return None
    return value


def _base_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").rstrip("/")


def _api_key() -> str:
    return (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
        or ""
    )


def is_roster_ready(write: bool = False) -> bool:
    return bool(_base_url() and _api_key())


def _paged_roster() -> list[dict]:
    key = _api_key()
    rows = []
    for start in range(0, 10_000_000, PAGE_SIZE):
        response = requests.get(
            f"{_base_url()}/rest/v1/tier_members",
            params={
                "select": "source_order,name,nickname,soop_id,elo_id,birth_date,gender,race,tier,affiliation,role,modified_at",
                "order": "source_order.asc,soop_id.asc",
            },
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range-Unit": "items",
                "Range": f"{start}-{start + PAGE_SIZE - 1}",
            },
            timeout=TIMEOUT,
        )
        if response.status_code not in (200, 206):
            raise RuntimeError(f"Supabase tier_members GET failed: HTTP {response.status_code} {(response.text or '')[:500]}")
        page = response.json()
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
    raise RuntimeError("Supabase tier_members pagination exceeded safety limit")


def load_roster_members() -> dict | None:
    if not is_roster_ready():
        return {}
    try:
        db_rows = _paged_roster()
    except Exception as exc:
        print(f"[오류] Supabase tier_members 읽기 실패: {exc}", file=sys.stderr)
        return None

    rows = {}
    for row in db_rows:
        soop_id = str(row.get("soop_id") or "").strip()
        nickname = str(row.get("nickname") or "").strip()
        if not soop_id or not nickname:
            continue
        gender = row.get("gender")
        rows[soop_id] = {
            "elo_name": str(row.get("name") or "").strip(),
            "nickname": nickname,
            "elo_id": row.get("elo_id"),
            "birthdate": str(row.get("birth_date") or "").strip() or None,
            "gender": GENDER_FROM_DB.get(gender, gender) if gender else None,
            "race": _clean(row.get("race")),
            "tier": str(_clean(row.get("tier"))) if _clean(row.get("tier")) is not None else None,
            "team": _clean(row.get("affiliation")),
            "role": str(row.get("role") or "").strip(),
            "info_updated_at": str(row.get("modified_at") or "").strip() or None,
        }
    return rows
