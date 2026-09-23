"""StarUniv와 공유하는 Supabase 로스터(tier_members) 접근 헬퍼.

필수 환경변수(쓰기 포함 자동화):
  SUPABASE_URL=https://<project>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=<service role key>

읽기만 할 때는 SUPABASE_ANON_KEY 또는 SUPABASE_PUBLISHABLE_KEY도 사용할 수 있다.
service role key는 GitHub Actions Secret/로컬 환경변수에서만 사용하고 저장소에 넣지 않는다.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

import requests

PAGE_SIZE = 1000
TIMEOUT = 30
GENDER_FROM_DB = {"남자": "m", "여자": "f", "m": "m", "f": "f"}
GENDER_TO_DB = {"m": "남자", "f": "여자", "남자": "남자", "여자": "여자"}
PENDING_TABLE_NAME = "tier_member_candidates"
PLACEHOLDER_VALUES = {"체크", "todo", "?", "미정", "", "null", "none", "n/a", "na"}


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in PLACEHOLDER_VALUES:
        return None
    return value


def _base_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").rstrip("/")


def _api_key(write: bool = False) -> str:
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if write:
        return service
    return service or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""


def is_roster_ready(write: bool = False) -> bool:
    return bool(_base_url() and _api_key(write=write))


def _headers(*, write: bool = False, extra: dict | None = None) -> dict:
    key = _api_key(write=write)
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _request(method: str, table: str, *, params=None, payload=None, write=False, headers=None):
    if not is_roster_ready(write=write):
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")
    url = f"{_base_url()}/rest/v1/{table}"
    resp = requests.request(
        method,
        url,
        params=params,
        json=payload,
        headers=_headers(write=write, extra=headers),
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201, 204, 206):
        body = (resp.text or "")[:500]
        raise RuntimeError(f"Supabase {table} {method} 실패: HTTP {resp.status_code} {body}")
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _paged_select(table: str, select: str, order: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        page = _request(
            "GET",
            table,
            params={"select": select, "order": order},
            headers={"Range-Unit": "items", "Range": f"{start}-{end}"},
        ) or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def load_roster_members() -> dict:
    """tier_members를 기존 Google Sheet helper와 같은 {soop_id: fields} 모양으로 반환."""
    if not is_roster_ready():
        return {}
    try:
        db_rows = _paged_select(
            "tier_members",
            "source_order,name,nickname,soop_id,elo_id,birth_date,gender,race,tier,affiliation,role,modified_at",
            "source_order.asc",
        )
    except Exception as exc:
        print(f"[오류] Supabase tier_members 읽기 실패: {exc}", file=sys.stderr)
        return None

    rows = {}
    for idx, row in enumerate(db_rows):
        soop_id = str(row.get("soop_id") or "").strip()
        nickname = str(row.get("nickname") or "").strip()
        if not soop_id or not nickname:
            continue
        if soop_id in rows:
            print(f"[경고] Supabase tier_members에 SOOP ID '{soop_id}'가 중복되어 뒤 행을 사용합니다.", file=sys.stderr)
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


def update_roster_members(update_rows: dict, append_rows: list | None = None,
                          delete_ids: set | None = None,
                          clear_info_updated_at: set | None = None) -> bool:
    """기존 write_sheet()와 같은 역할. 자동화에서는 기존 행의 일부 필드와 수정일만 갱신한다."""
    append_rows = append_rows or []
    delete_ids = delete_ids or set()
    clear_info_updated_at = clear_info_updated_at or set()
    if not (update_rows or append_rows or delete_ids or clear_info_updated_at):
        return True
    if not is_roster_ready(write=True):
        print("[경고] SUPABASE_SERVICE_ROLE_KEY가 없어 로스터 쓰기를 건너뜁니다.", file=sys.stderr)
        return False

    try:
        for soop_id, fields in update_rows.items():
            payload = {}
            mapping = {
                "elo_name": "name",
                "nickname": "nickname",
                "elo_id": "elo_id",
                "birthdate": "birth_date",
                "race": "race",
                "tier": "tier",
                "team": "affiliation",
                "role": "role",
            }
            for old_key, db_key in mapping.items():
                value = fields.get(old_key)
                if value not in (None, ""):
                    payload[db_key] = value
            if fields.get("gender") not in (None, ""):
                payload["gender"] = GENDER_TO_DB.get(fields.get("gender"), fields.get("gender"))
            if payload:
                _request(
                    "PATCH", "tier_members",
                    params={"soop_id": f"eq.{soop_id}"},
                    payload=payload, write=True,
                    headers={"Content-Type": "application/json", "Prefer": "return=minimal"},
                )

        # 이 프로젝트에서는 append_rows를 자동 승인 용도로 사용하지 않는다.
        # 혹시 호출되더라도 source_order/검수 없이 tier_members에 넣지 않고 후보 테이블로 보낸다.
        if append_rows:
            append_pending_members(append_rows)

        for soop_id in delete_ids:
            _request(
                "DELETE", "tier_members", params={"soop_id": f"eq.{soop_id}"},
                write=True, headers={"Prefer": "return=minimal"},
            )

        for soop_id in clear_info_updated_at:
            _request(
                "PATCH", "tier_members", params={"soop_id": f"eq.{soop_id}"},
                payload={"modified_at": None}, write=True,
                headers={"Content-Type": "application/json", "Prefer": "return=minimal"},
            )
        return True
    except Exception as exc:
        print(f"[오류] Supabase tier_members 쓰기 실패: {exc}", file=sys.stderr)
        return False


def load_pending_members() -> dict:
    if not is_roster_ready():
        return {}
    try:
        rows = _paged_select(
            PENDING_TABLE_NAME,
            "id,nickname,elo_id,gender,race,tier,affiliation,source,found_at",
            "found_at.desc,id.asc",
        )
    except Exception as exc:
        print(f"[오류] Supabase {PENDING_TABLE_NAME} 읽기 실패: {exc}", file=sys.stderr)
        return None
    return {
        str(row.get("id")): {
            "nickname": row.get("nickname") or "",
            "elo_id": str(row.get("elo_id")) if row.get("elo_id") is not None else None,
            "gender": GENDER_FROM_DB.get(row.get("gender"), row.get("gender")) if row.get("gender") else "",
            "race": row.get("race"),
            "tier": row.get("tier"),
            "team": row.get("affiliation"),
            "source": row.get("source") or "",
            "found_at": str(row.get("found_at") or ""),
        }
        for row in rows if row.get("id")
    }


def append_pending_members(candidates: list) -> bool:
    if not candidates:
        return True
    if not is_roster_ready(write=True):
        print(f"[경고] SUPABASE_SERVICE_ROLE_KEY가 없어 {PENDING_TABLE_NAME} 쓰기를 건너뜁니다.", file=sys.stderr)
        return False
    payload = []
    for c in candidates:
        cid = str(c.get("id") or "").strip()
        if not cid:
            continue
        elo_id = c.get("elo_id")
        try:
            elo_id = int(float(elo_id)) if elo_id not in (None, "") else None
        except (TypeError, ValueError, OverflowError):
            elo_id = None
        payload.append({
            "id": cid,
            "nickname": c.get("nickname") or "",
            "elo_id": elo_id,
            "gender": GENDER_TO_DB.get(c.get("gender"), c.get("gender")) if c.get("gender") else None,
            "race": c.get("race") or None,
            "tier": str(c.get("tier")) if c.get("tier") not in (None, "") else None,
            "affiliation": c.get("team") or None,
            "source": c.get("source") or "",
            "found_at": c.get("found_at") or None,
        })
    if not payload:
        return True
    try:
        _request(
            "POST", PENDING_TABLE_NAME,
            params={"on_conflict": "id"}, payload=payload, write=True,
            headers={
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        return True
    except Exception as exc:
        print(f"[오류] Supabase {PENDING_TABLE_NAME} 후보 추가 실패: {exc}", file=sys.stderr)
        return False
