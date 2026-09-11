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
    is_sheet_ready, load_sheet_members, write_sheet, normalize_elo_id,
    load_pending_members, append_pending_members, PENDING_SHEET_NAME
)
from fetch_poonggo_data import fetch_poonggo_monthly
from fetch_eloboard_data import aggregate_period_data

MEMBERS_PATH = ROOT / "data" / "members.json"
OUTPUT_PATH = ROOT / "data" / "latest.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
APPLIED_CORRECTIONS_PATH = ROOT / "data" / "archive_corrections_applied.json"
CONFIRMED_MONTHS_PATH = ROOT / "data" / "archive_month_confirmed.json"
PRUNE_GRACE_MONTHS = 6
# 워크플로우가 아주 오랫동안(수개월) 안 돌다가 재개된 극단적인 경우에도
# 한 번에 너무 많은 달을 몰아서 처리하다 25분 타임아웃을 넘기지 않도록
# 상한을 둔다 - 다 못 채운 나머지 달은 다음 실행에서 계속 이어서 처리된다.
MAX_MONTHS_TO_CONFIRM_PER_RUN = 12

# --- 아카이브 폴더 구조화 헬퍼 ---
def get_archive_path(date_str: str) -> Path:
    """YYYY-MM-DD 형태의 날짜를 받아 연/월 단위 폴더 경로를 반환합니다."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # date_str을 그대로 파일명에 쓰기 전에 파싱 결과로 다시 만들어 쓴다 -
    # strptime을 통과하는 값은 안전하지만, 경로 조립에 외부 문자열을 직접
    # 끼워넣는 패턴 자체를 남겨두지 않는 편이 낫다.
    normalized = dt.strftime("%Y-%m-%d")
    return ARCHIVE_DIR / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{normalized}.json"

def iter_archive_files():
    """아카이브 디렉터리에서 'YYYY-MM-DD.json' 형태의 파일만 (날짜, 경로)로
    돌려준다. rglob("*.json")을 그대로 쓰면 나중에 누가 archive 폴더에
    메모용 json이라도 하나 넣는 순간 그 파일명이 날짜로 취급돼 비교/수정
    대상이 되어버린다."""
    if not ARCHIVE_DIR.exists():
        return
    for path in ARCHIVE_DIR.rglob("*.json"):
        stem = path.stem
        if len(stem) != 10:
            continue
        try:
            datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        yield stem, path
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
        elo_id_str = normalize_elo_id(item.get("id"))
        if elo_id_str and elo_id_str not in existing_elo_ids:
            try:
                elo_id_int = int(elo_id_str)
            except (ValueError, TypeError):
                # EloBoard가 숫자가 아닌 player_id를 내려주면 예전 코드는
                # 여기서 그대로 ValueError로 죽었다 - 그 한 건 때문에
                # update_data.py 전체가 실패하면서 그날 수집분이 통째로
                # 날아간다. 후보 한 명을 포기하는 쪽이 훨씬 싸다.
                print(f"[경고] elo_id가 숫자가 아니라 신규 후보에서 제외합니다: {elo_id_str!r}",
                      file=sys.stderr)
                continue
            new_member = {
                "id": f"elo_{elo_id_str}",
                "nickname": f"미상(elo_{elo_id_str})",
                "elo_id": elo_id_int,
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

    for file_date, archive_path in iter_archive_files():
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
    # 키를 normalize_elo_id로 통일해야, 조회할 때 쓰는 members.json의
    # elo_id(정수)와 문자열 표현이 항상 같은 모양으로 만난다.
    return {normalize_elo_id(item.get("id")): item for item in sponsor_list if item.get("id")}

def _iter_months(y1, m1, y2, m2):
    """(y1,m1)부터 (y2,m2) 바로 전달까지의 (year, month) 튜플을 순서대로
    반환한다. (y2,m2)는 포함하지 않는다(아직 진행 중인 이번 달이라 확정
    대상이 아니므로). 워크플로우가 여러 달을 건너뛰고 재개된 경우, 중간에
    낀 달들도 전부 여기서 나온다(예전엔 prev_month 딱 하나만 봤어서 중간
    달들이 통째로 스킵됐었다)."""
    y, m = y1, m1
    while (y, m) < (y2, m2):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1

def confirm_month(target_year, target_month, all_ids, now, existing_elo_ids, new_members_acc,
                   skip_new_member_detection=False, prev_flags=None):
    """지정한 (target_year, target_month)의 마지막 날 아카이브를, 그 달이
    완전히 끝난 뒤 뒤늦게 들어온 데이터로 사후 보정한다.

    prev_flags(예: {"balloon": True, "sponsor": False})에 이미 성공한
    부분이 있으면 그 부분은 다시 시도하지 않는다 - 예전 구현은 이 확정
    시도를 "월이 바뀐 첫 실행, 딱 한 번"만 허용해서, 그때 부분적으로만
    실패해도 재시도 기회 자체가 없이 그 달 수치가 영구히 부정확한 채로
    남는 문제가 있었다. 이제는 실패한 부분의 완료 여부를 호출부가
    영속화해서, 매 실행마다 "아직 안 끝난 부분만" 계속 재시도한다.

    반환값은 이번 호출 뒤의 최종 완료 상태 {"balloon": bool, "sponsor": bool}.

    알려진 한계: 별풍선(풍고) 보정은 오늘 시점 로스터에서 뽑은 soop_id로
    풍고에 물어본 결과를, 아카이브에 그 당시 저장돼있던 soop_id와 매칭한다.
    그 사이 누군가의 soop_id 자체가 바뀌었다면(오타 수정, 계정 이전 등)
    이 함수는 그 사람의 별풍선을 갱신하지 못하고 조용히 넘어간다 - id
    변경 이력을 별도로 추적하는 장치가 없는 한 일반적으로 고치기 어려운
    한계라 이번 개선에서는 다루지 않고 그대로 남겨둔다."""
    prev_flags = prev_flags or {}
    last_day = last_day_of_month(target_year, target_month)
    archive_path = get_archive_path(f"{target_year:04d}-{target_month:02d}-{last_day:02d}")
    if not archive_path.exists():
        # 그 달 마지막 날 아카이브 자체가 없으면(그 달엔 워크플로우가 아예
        # 안 돌았음) 확정할 대상이 없는 것 - 완료로 간주해서 다음 실행에서
        # 또 시도하지 않게 한다.
        return {"balloon": True, "sponsor": True}

    archive = safe_read_json(archive_path, default=None)
    if archive is None:
        return {"balloon": prev_flags.get("balloon", False), "sponsor": prev_flags.get("sponsor", False)}

    changed = False
    sponsor_changed = False
    balloon_done = prev_flags.get("balloon", False)
    sponsor_done = prev_flags.get("sponsor", False)

    if not balloon_done:
        balloon_data = fetch_poonggo_monthly(target_year, target_month, all_ids)
        if balloon_data is not None:
            for m in archive.get("members", []):
                src = balloon_data.get(m.get("id"))
                if src:
                    m["balloons"] = src["balloons"]
                    m["broadcast_seconds"] = src["broadcast_seconds"]
                    m["cumulative_viewers"] = src["cumulative_viewers"]
                    changed = True
            balloon_done = True
        # balloon_data가 None이면 fetch_poonggo_monthly가 실제로 실패한
        # 것(빈 결과 {}와 구분됨) - balloon_done을 True로 안 만들고 다음
        # 실행에서 다시 시도되게 둔다.

    if not sponsor_done:
        start_date = f"{target_year:04d}-{target_month:02d}-01"
        end_date = f"{target_year:04d}-{target_month:02d}-{last_day:02d}"
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
                src = lookup.get(normalize_elo_id(elo_id)) if elo_id is not None else None
                new_wins = src["sponsor_wins"] if src else 0
                new_losses = src["sponsor_losses"] if src else 0
                if m.get("sponsor_wins") != new_wins or m.get("sponsor_losses") != new_losses:
                    m["sponsor_wins"] = new_wins
                    m["sponsor_losses"] = new_losses
                    changed = True
                    sponsor_changed = True
            sponsor_done = True
        # sponsor_list가 빈 리스트인 건 aggregate_period_data()의 반환값
        # 특성상 "그 기간 스폰전적이 진짜 0건"과 "API 실패"를 구분할 수
        # 없다(둘 다 [] 반환) - 그래서 여기선 보수적으로 sponsor_done을
        # True로 만들지 않는다. 실제로 0건인 달이면 다음 실행에서 조회를
        # 한 번 더 하는 정도의 비용만 있을 뿐이라, "실패를 성공으로
        # 착각해서 영영 안 고쳐지는 것"보다 훨씬 안전한 쪽을 택했다.

    if changed:
        archive["updated_at"] = now.strftime(DATETIME_FORMAT)
        if sponsor_changed:
            archive["sponsor_updated_at"] = now.strftime(DATETIME_FORMAT)
        atomic_write_json(archive_path, archive)

    return {"balloon": balloon_done, "sponsor": sponsor_done}

def main():
    if not MEMBERS_PATH.exists():
        print(f"[오류] {MEMBERS_PATH}가 없습니다 - convert_members.py가 먼저 성공했는지 확인하세요.",
              file=sys.stderr)
        sys.exit(1)

    # 예전엔 json.load를 그대로 썼는데, members.json이 (이전 실행이 중간에
    # 죽어서) 깨져 있으면 여기서 JSONDecodeError로 죽으면서 원인 메시지가
    # 하나도 안 남았다.
    config = safe_read_json(MEMBERS_PATH, default=None)
    if not isinstance(config, dict):
        print(f"[오류] {MEMBERS_PATH}를 읽을 수 없거나 형식이 올바르지 않습니다.", file=sys.stderr)
        sys.exit(1)

    members = validate_and_clean_members(config.get("members", []))
    if not members:
        # 여기서 계속 진행하면 "회원 0명"짜리 latest.json을 정상 결과인 것처럼
        # 저장해서, 사이트가 통째로 빈 화면이 되고 그 상태가 아카이브로도
        # 굳어버린다. 기존 데이터를 지키려면 아무것도 안 쓰고 멈춰야 한다.
        print("[오류] 유효한 회원이 0명입니다 - latest.json을 건드리지 않고 중단합니다.", file=sys.stderr)
        sys.exit(1)

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
    # 시트 자격 증명 자체가 없으면 "이미 등록된 elo_id 목록"을 알 방법이
    # 없다. 예전 코드는 이 경우 existing_elo_ids를 빈 집합으로 둔 채 판별을
    # 그대로 진행해서, 스폰전적에 나온 전원을 "신규 후보"로 잡고 "N명 추가
    # 완료" 로그까지 남겼다(실제 추가는 인증이 없어 조용히 건너뛰어졌으니,
    # 로그만 사실과 다른 상태였다). 알 수 없으면 판별을 건너뛴다.
    skip_new_member_detection = not is_sheet_ready()
    if skip_new_member_detection:
        print("[알림] 구글 시트 설정이 없어 신규 후보 판별을 건너뜁니다.")
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
                normalize_elo_id(fields["elo_id"]) for fields in sheet_members.values()
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
                    normalize_elo_id(fields["elo_id"]) for fields in pending_members.values()
                    if fields.get("elo_id")
                }
    new_members_acc = {}

    archive_previous_day_if_needed(prev_latest, today_date_str)

    if prev_year and prev_month:
        confirmed_months = safe_read_json(CONFIRMED_MONTHS_PATH, default={})
        if confirmed_months is None:
            # 이 파일 읽기가 실패하면 "이미 다 확정됐다"고 잘못 믿고 건너뛰는
            # 것보다는, 차라리 이번 실행에서 다시 시도하는 게(중복 시도는
            # 비용만 좀 들 뿐 데이터를 틀리게 만들진 않는다) 안전하다.
            confirmed_months = {}

        months_to_confirm = list(_iter_months(prev_year, prev_month, year, month))
        if len(months_to_confirm) > MAX_MONTHS_TO_CONFIRM_PER_RUN:
            print(f"[경고] 밀린 월 확정이 {len(months_to_confirm)}개월치라 "
                  f"이번엔 최근 {MAX_MONTHS_TO_CONFIRM_PER_RUN}개월만 처리하고, "
                  f"나머지는 다음 실행에서 이어서 처리합니다.", file=sys.stderr)
            months_to_confirm = months_to_confirm[-MAX_MONTHS_TO_CONFIRM_PER_RUN:]

        for (cy, cm) in months_to_confirm:
            key = f"{cy:04d}-{cm:02d}"
            flags = confirmed_months.get(key, {})
            if flags.get("balloon") and flags.get("sponsor"):
                continue
            new_flags = confirm_month(cy, cm, all_ids, now, existing_elo_ids, new_members_acc,
                                       skip_new_member_detection, flags)
            confirmed_months[key] = new_flags

        atomic_write_json(CONFIRMED_MONTHS_PATH, confirmed_months)

    applied_correction_ids = apply_member_updates_to_archives(members, today_date_str)

    print("[수집] 풍고 별풍선 및 엘로보드 스폰전적 병렬 수집 시작...")
    start_date, end_date = get_month_date_range(now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_balloon = executor.submit(fetch_poonggo_monthly, year, month, all_ids)
        future_sponsor = executor.submit(aggregate_period_data, start_date, end_date)

        # 두 future 모두 result()를 try로 감싼다. 예전엔 별풍선 쪽만 무방비라,
        # fetch_poonggo_monthly가 예외를 던지면(응답 형식 이상 등) 그 예외가
        # 그대로 튀어나와 아래 "기존 값 유지" 로직을 한 줄도 못 타고 죽었다.
        try:
            balloon_data = future_balloon.result()
        except Exception as e:
            print(f"[오류] 풍고 수집 중 예외: {e}", file=sys.stderr)
            balloon_data = None
        try:
            sponsor_list = future_sponsor.result()
        except Exception as e:
            print(f"[경고] 엘로보드 수집 중 오류: {e} - 기존 스폰전적 유지", file=sys.stderr)
            sponsor_list = []

    if balloon_data is None:
        # 여기서 죽더라도, 이 함수가 지금까지 디스크에 써둔 것(아카이브 사후
        # 보정, 월 확정 결과)은 이미 올바른 데이터다. 워크플로우가 이 스텝을
        # continue-on-error로 받아 뒤 단계(페이지 생성/커밋)를 계속 진행하고,
        # 마지막에 별도 스텝이 이 실패를 잡아 job을 실패로 표시한다
        # (.github/workflows/updatestats.yml 참고). 그래야 "실패 알림"과
        # "이미 끝낸 작업 보존"을 둘 다 얻는다.
        print("[오류] 별풍선 데이터를 가져오지 못해 latest.json을 갱신하지 않습니다 "
              "(기존 파일은 그대로 보존됩니다).", file=sys.stderr)
        sys.exit(1)

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
        sd = sponsor_data.get(normalize_elo_id(elo_id)) if elo_id is not None else None

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
        if write_sheet({}, [], clear_info_updated_at=applied_correction_ids):
            print(f"[완료] 소급 정정이 끝난 {len(applied_correction_ids)}명의 수정일을 비웠습니다.")
        else:
            print(f"[경고] 소급 정정이 끝난 {len(applied_correction_ids)}명의 수정일을 시트에서 "
                  f"비우지 못했습니다 - 시트에 '수정일'이 남아 있을 수 있습니다(다음 실행에서는 "
                  f"이미 적용된 것으로 간주되므로 필요하면 수동으로 지워주세요).", file=sys.stderr)

    if new_members_acc:
        # 실제로 써졌을 때만 "완료"라고 말한다.
        if append_pending_members(list(new_members_acc.values())):
            print(f"[완료] 총 {len(new_members_acc)}명의 신규 후보가 '{PENDING_SHEET_NAME}' 시트에 추가되었습니다 "
                  f"(검토 후 members 시트로 옮겨주세요).")
        else:
            print(f"[경고] 신규 후보 {len(new_members_acc)}명을 '{PENDING_SHEET_NAME}' 시트에 "
                  f"추가하지 못했습니다 - 다음 실행에서 다시 시도됩니다.", file=sys.stderr)

if __name__ == "__main__":
    main()
