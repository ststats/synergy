"""
synergy 프로젝트의 데이터 갱신 오케스트레이터.
"""

import sys
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _common import (
    ROOT, DATETIME_FORMAT, kst_now, last_day_of_month, get_month_date_range,
    atomic_write_json, safe_read_json, validate_and_clean_members,
    is_sheet_ready, load_sheet_members, write_sheet,
    load_pending_members, append_pending_members, PENDING_SHEET_NAME
)
from fetch_poonggo_data import fetch_poonggo_monthly
from fetch_eloboard_data import aggregate_period_data

MEMBERS_PATH = ROOT / "data" / "members.json"
OUTPUT_PATH = ROOT / "data" / "latest.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
APPLIED_CORRECTIONS_PATH = ROOT / "data" / "archive_corrections_applied.json"
PRUNE_GRACE_MONTHS = 6

# --- 아카이브 폴더 구조화 헬퍼 ---
def get_archive_path(date_str: str) -> Path:
    """YYYY-MM-DD 형태의 날짜를 받아 연/월 단위 폴더 경로를 반환합니다."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    path = ARCHIVE_DIR / str(dt.year) / f"{dt.month:02d}" / f"{date_str}.json"
    return path
# ---------------------------------

def _collect_unknown_elo_players(sponsor_list: list, existing_elo_ids: set, today_date_str: str) -> dict:
    """스폰전적에만 등장하고 members 시트에도, new_members(대기) 시트에도
    없는 elo_id를 찾아 "신규 후보"로 만든다. 예전에는 이 결과를 곧바로
    members 시트에 "미상(elo_...)" 임시 프로필로 등록했지만, 검증되지 않은
    프로필이 실제 로스터 시트에 섞이는 걸 막기 위해 이제는 new_members
    시트로 보낸다 - 관리자가 실제 SOOP ID/닉네임을 확인해서 검토한 뒤
    수동으로 members 시트에 옮겨야 한다.
    existing_elo_ids에는 호출부에서 members 시트뿐 아니라 new_members
    시트에 이미 대기 중인 elo_id도 함께 넣어줘야, 검토가 아직 안 끝난
    같은 후보가 매 실행마다 new_members에 중복으로 쌓이지 않는다."""
    new_members = {}
    for item in sponsor_list:
        elo_id_str = item.get("id")
        if elo_id_str and elo_id_str not in existing_elo_ids:
            new_member = {
                "id": f"elo_{elo_id_str}",
                "nickname": f"미상(elo_{elo_id_str})",
                "elo_id": int(elo_id_str),
                "gender": "",
                "race": "",
                "tier": "",
                "team": "",
                "source": "elo_unknown",
                "found_at": today_date_str,
            }
            new_members[elo_id_str] = new_member
            existing_elo_ids.add(elo_id_str)
            print(f"[알림] 새로운 신규 후보 발견됨: {new_member['nickname']}")
    return new_members

def archive_previous_day_if_needed(prev_latest: dict, new_date_str: str):
    if not prev_latest:
        return
    prev_date = prev_latest.get("date")
    if not prev_date or prev_date == new_date_str:
        return
    archive_path = get_archive_path(prev_date)
    if not archive_path.exists():
        atomic_write_json(archive_path, prev_latest)

def _prune_stale_corrections(applied: dict, updates: dict, today_date_str: str) -> dict:
    cutoff = (
        datetime.strptime(today_date_str, "%Y-%m-%d") - timedelta(days=PRUNE_GRACE_MONTHS * 30)
    ).strftime("%Y-%m-%d")
    pruned = {}
    removed = 0
    for mid, upd in applied.items():
        if mid in updates or upd.get("update_date", "") >= cutoff:
            pruned[mid] = upd
        else:
            removed += 1
    if removed:
        print(f"[정리] archive_corrections_applied.json에서 오래된 기록 {removed}건 정리됨")
    return pruned

def apply_member_updates_to_archives(members: list, today_date_str: str) -> set:
    updates = {}
    for m in members:
        update_date = m.get("info_updated_at")
        if update_date and m.get("id"):
            updates[m["id"]] = {
                "update_date": update_date,
                "team": m.get("team"),
                "tier": m.get("tier"),
                "role": m.get("role"),
                "race": m.get("race"),
                "nickname": m.get("nickname"),
                "elo_id": m.get("elo_id"),
            }

    if not updates or not ARCHIVE_DIR.exists():
        return set()

    applied = safe_read_json(APPLIED_CORRECTIONS_PATH, default={})
    pending = {mid: upd for mid, upd in updates.items() if applied.get(mid) != upd}
    if not pending:
        return set()

    earliest_update_date = min(u["update_date"] for u in pending.values())

    # 하위 폴더까지 스캔하기 위해 rglob 사용
    for archive_path in ARCHIVE_DIR.rglob("*.json"):
        file_date = archive_path.stem
        if file_date < earliest_update_date:
            continue
        changed = False
        arch_data = safe_read_json(archive_path, default=None)
        if arch_data is None:
            continue

        for am in arch_data.get("members", []):
            mid = am.get("id")
            if mid in pending:
                upd = pending[mid]
                if file_date >= upd["update_date"]:
                    for key in ["team", "tier", "role", "race", "nickname", "elo_id"]:
                        if am.get(key) != upd[key]:
                            am[key] = upd[key]
                            changed = True

        if changed:
            atomic_write_json(archive_path, arch_data)
            print(f"[소급적용] {file_date}.json 파일에 멤버 정보 업데이트 반영됨")

    applied.update(pending)
    applied = _prune_stale_corrections(applied, updates, today_date_str)
    atomic_write_json(APPLIED_CORRECTIONS_PATH, applied)
    return set(pending.keys())

def _index_by_elo_id(sponsor_list: list) -> dict:
    return {item["id"]: item for item in sponsor_list if item.get("id")}

def confirm_previous_month_if_needed(prev_year, prev_month, new_year, new_month, all_ids, now, existing_elo_ids, new_members_acc, skip_new_member_detection=False):
    if not prev_year or not prev_month or (prev_year, prev_month) == (new_year, new_month):
        return
    last_day = last_day_of_month(prev_year, prev_month)
    archive_path = get_archive_path(f"{prev_year:04d}-{prev_month:02d}-{last_day:02d}")
    if not archive_path.exists():
        return

    archive = safe_read_json(archive_path, default=None)
    if archive is None:
        return

    changed = False
    sponsor_changed = False

    balloon_data = fetch_poonggo_monthly(prev_year, prev_month, all_ids)
    if balloon_data:
        for m in archive.get("members", []):
            src = balloon_data.get(m.get("id"))
            if src:
                m["balloons"] = src["balloons"]
                m["broadcast_seconds"] = src["broadcast_seconds"]
                m["cumulative_viewers"] = src["cumulative_viewers"]
                changed = True

    start_date = f"{prev_year:04d}-{prev_month:02d}-01"
    end_date = f"{prev_year:04d}-{prev_month:02d}-{last_day:02d}"
    try:
        sponsor_list = aggregate_period_data(start_date, end_date)
    except Exception:
        sponsor_list = []
        
    if sponsor_list:
        if not skip_new_member_detection:
            new_members_acc.update(_collect_unknown_elo_players(sponsor_list, existing_elo_ids, now.strftime("%Y-%m-%d")))
        lookup = _index_by_elo_id(sponsor_list)
        for m in archive.get("members", []):
            elo_id = m.get("elo_id")
            src = lookup.get(str(elo_id)) if elo_id is not None else None
            new_wins = src["sponsor_wins"] if src else 0
            new_losses = src["sponsor_losses"] if src else 0
            if m.get("sponsor_wins") != new_wins or m.get("sponsor_losses") != new_losses:
                m["sponsor_wins"] = new_wins
                m["sponsor_losses"] = new_losses
                changed = True
                sponsor_changed = True

    if changed:
        archive["updated_at"] = now.strftime(DATETIME_FORMAT)
        if sponsor_changed:
            archive["sponsor_updated_at"] = now.strftime(DATETIME_FORMAT)
        atomic_write_json(archive_path, archive)

def main():
    if not MEMBERS_PATH.exists():
        sys.exit(1)

    with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    members = validate_and_clean_members(config.get("members", []))
    all_ids = [m["id"] for m in members]

    now = kst_now()
    year, month = now.year, now.month
    today_date_str = now.strftime("%Y-%m-%d")

    prev_latest = safe_read_json(OUTPUT_PATH, default=None) if OUTPUT_PATH.exists() else None
    prev_year = prev_month = None
    sponsor_updated_at = sponsor_month = None
    existing_sponsor = {}
    
    if prev_latest:
        prev_year, prev_month = prev_latest.get("year"), prev_latest.get("month")
        sponsor_updated_at = prev_latest.get("sponsor_updated_at")
        sponsor_month = prev_latest.get("sponsor_month")
        current_month_str = f"{year:04d}-{month:02d}"
        if sponsor_month == current_month_str:
            for om in prev_latest.get("members", []):
                mid = om.get("id")
                if mid:
                    existing_sponsor[mid] = {
                        "sponsor_wins": om.get("sponsor_wins", 0),
                        "sponsor_losses": om.get("sponsor_losses", 0),
                    }

    existing_elo_ids = set()
    skip_new_member_detection = False
    if is_sheet_ready():
        sheet_members = load_sheet_members()
        if sheet_members is None:
            # 읽기 실패를 "기존 회원 0명"으로 착각하면, 이미 등록된 회원
            # 전원을 "신규 후보"로 오판해 new_members 시트에 통째로
            # 밀어넣는 사고로 이어진다. 이번 실행에서는 신규 후보 판별
            # 자체를 건너뛰고(별풍선/스폰전적 갱신 등 나머지 작업은
            # 평소대로 계속 진행), 원인(탭 이름/권한 등)을 알 수 있게
            # 경고를 남긴다.
            print("[오류] members 시트를 읽지 못해 이번 실행에서는 신규 후보 판별을 건너뜁니다.", file=sys.stderr)
            skip_new_member_detection = True
        else:
            existing_elo_ids = {
                str(fields["elo_id"]) for fields in sheet_members.values()
                if fields.get("elo_id") is not None
            }
            # new_members(대기) 시트에 이미 올라와 검토를 기다리고 있는
            # elo_id도 합쳐야, 아직 검토 안 끝난 같은 후보가 매 실행마다
            # 또 대기 시트에 중복으로 쌓이는 걸 막을 수 있다.
            pending_members = load_pending_members()
            if pending_members is None:
                print(f"[오류] '{PENDING_SHEET_NAME}' 시트를 읽지 못해 이번 실행에서는 신규 후보 판별을 건너뜁니다.", file=sys.stderr)
                skip_new_member_detection = True
            else:
                existing_elo_ids |= {
                    fields["elo_id"] for fields in pending_members.values()
                    if fields.get("elo_id")
                }
    new_members_acc = {}

    archive_previous_day_if_needed(prev_latest, today_date_str)
    confirm_previous_month_if_needed(prev_year, prev_month, year, month, all_ids, now, existing_elo_ids, new_members_acc, skip_new_member_detection)
    applied_correction_ids = apply_member_updates_to_archives(members, today_date_str)

    print(f"[수집] 풍고 별풍선 및 엘로보드 스폰전적 병렬 수집 시작...")
    start_date, end_date = get_month_date_range(now)
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_balloon = executor.submit(fetch_poonggo_monthly, year, month, all_ids)
        future_sponsor = executor.submit(aggregate_period_data, start_date, end_date)
        
        balloon_data = future_balloon.result()
        try:
            sponsor_list = future_sponsor.result()
        except Exception as e:
            print(f"[경고] 엘로보드 수집 중 오류: {e} - 기존 스폰전적 유지", file=sys.stderr)
            sponsor_list = []

    if balloon_data is None:
        raise SystemExit("[오류] 별풍선 데이터를 가져오지 못했습니다.")

    sponsor_data = {}
    sponsor_collection_succeeded = False
        
    if sponsor_list:
        if not skip_new_member_detection:
            new_members_acc.update(_collect_unknown_elo_players(sponsor_list, existing_elo_ids, today_date_str))
        sponsor_data = _index_by_elo_id(sponsor_list)
        sponsor_collection_succeeded = True
        sponsor_updated_at = now.strftime(DATETIME_FORMAT)
        sponsor_month = f"{year:04d}-{month:02d}"

    out_members = []
    for m in members:
        member_id = m.get("id")
        elo_id = m.get("elo_id")
        bd = balloon_data.get(member_id) if member_id else None
        sd = sponsor_data.get(str(elo_id)) if elo_id is not None else None

        if sd:
            sponsor_wins, sponsor_losses = sd["sponsor_wins"], sd["sponsor_losses"]
        elif sponsor_collection_succeeded:
            sponsor_wins, sponsor_losses = 0, 0
        else:
            existing = existing_sponsor.get(member_id, {"sponsor_wins": 0, "sponsor_losses": 0})
            sponsor_wins, sponsor_losses = existing["sponsor_wins"], existing["sponsor_losses"]

        out_members.append({
            "id": member_id,
            "elo_id": elo_id,
            "nickname": m.get("nickname") or member_id,
            "role": m.get("role"),
            "team": m.get("team"),
            "race": m.get("race"),
            "tier": m.get("tier"),
            "balloons": bd["balloons"] if bd else 0,
            "broadcast_seconds": bd["broadcast_seconds"] if bd else 0,
            "cumulative_viewers": bd["cumulative_viewers"] if bd else 0,
            "sponsor_wins": sponsor_wins,
            "sponsor_losses": sponsor_losses,
        })

    result = {
        "updated_at": now.strftime(DATETIME_FORMAT),
        "date": today_date_str,
        "year": year,
        "month": month,
        "members": out_members,
    }
    if sponsor_updated_at:
        result["sponsor_updated_at"] = sponsor_updated_at
    if sponsor_month:
        result["sponsor_month"] = sponsor_month

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUTPUT_PATH, result)

    print(f"[완료] {OUTPUT_PATH.name} 갱신됨 (별풍선 {len(balloon_data)}명, 스폰전적 {len(sponsor_data)}명)")

    if applied_correction_ids:
        write_sheet({}, [], clear_info_updated_at=applied_correction_ids)
        print(f"[완료] 소급 정정이 끝난 {len(applied_correction_ids)}명의 수정일을 비웠습니다.")

    if new_members_acc:
        append_pending_members(list(new_members_acc.values()))
        print(f"[완료] 총 {len(new_members_acc)}명의 신규 후보가 '{PENDING_SHEET_NAME}' 시트에 추가되었습니다 "
              f"(검토 후 members 시트로 옮겨주세요).")

if __name__ == "__main__":
    main()
