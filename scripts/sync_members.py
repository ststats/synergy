"""
EloBoard의 티어 목록 API(/api/tiers)를 조회해서 구글 시트를 최신 정보로 동기화하는 스크립트.
"""

import sys
import argparse

from _common import (
    fetch_json, is_sheet_ready, load_sheet_members, write_sheet, kst_now,
    load_pending_members, append_pending_members, PENDING_SHEET_NAME
)

# API가 티어 목록을 통째로 비워서(또는 일부만) 내려주는 사고가 났을 때,
# "시트에 있던 사람 전원이 API에 없다 -> 전원 신규 후보"가 되거나 반대로
# 대량 갱신이 쏟아지는 것을 막는 안전판. 한 번의 실행에서 이보다 많은
# 신규 후보가 잡히면 데이터 쪽 사고로 보고 멈춘다.
MAX_NEW_CANDIDATES_PER_RUN = 100

TIERS_URL = "https://eloboard.co.kr/api/tiers"

UPDATE_EXISTING_NICKNAME = False
UPDATE_EXISTING_RACE = False
UPDATE_EXISTING_TIER = False
UPDATE_EXISTING_TEAM = False

RACE_MAP = {
    "T": "테란",
    "Z": "저그",
    "P": "프로토스",
    "R": "랜덤",
}

def normalize_tier(label):
    if isinstance(label, str) and label.endswith("티어"):
        return label[:-len("티어")].strip()
    return label

def flatten_players(api_data) -> list:
    """API 응답에서 (티어 라벨이 붙은) 선수 목록을 평탄화한다.

    응답 구조가 조금이라도 예상과 다르면 예전 코드는 AttributeError/TypeError로
    죽었다 - 외부 API 형식 변경 한 번에 시트 동기화가 통째로 멈춘다. 각
    단계에서 타입을 확인하고, 이상한 항목만 건너뛴다."""
    players = []
    if not isinstance(api_data, dict):
        print(f"[경고] 티어 API 응답이 dict가 아닙니다({type(api_data).__name__})", file=sys.stderr)
        return players
    tiers = api_data.get("tiers")
    if not isinstance(tiers, list):
        print("[경고] 티어 API 응답에 'tiers' 리스트가 없습니다.", file=sys.stderr)
        return players
    for tier_obj in tiers:
        if not isinstance(tier_obj, dict):
            continue
        tier_label = normalize_tier(tier_obj.get("label"))
        tier_players = tier_obj.get("players")
        if not isinstance(tier_players, list):
            continue
        for player in tier_players:
            if not isinstance(player, dict):
                continue
            players.append({**player, "current_tier": tier_label})
    return players

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="파일을 실제로 바꾸지 않고 변경될 내용만 출력")
    parser.add_argument(
        "--allow-bulk-new", action="store_true",
        help=f"신규 후보가 {MAX_NEW_CANDIDATES_PER_RUN}명을 넘어도 진행합니다 "
             f"(빈 시트에서 처음 채울 때처럼, 대량 신규가 정상인 경우에만 사용)",
    )
    args = parser.parse_args(argv)

    if not is_sheet_ready():
        print("[오류] 구글 시트 자격 증명이 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    member_map = load_sheet_members()
    if member_map is None:
        print("[오류] members 시트를 읽지 못해 동기화를 중단합니다 "
              "(읽기 실패를 '기존 회원 0명'으로 착각해 전원을 신규로 "
              "오판하는 사고를 막기 위함 - 탭 이름/GOOGLE_SHEET_ID/권한을 확인하세요).",
              file=sys.stderr)
        sys.exit(1)
    # soop_id 대소문자/공백 차이 때문에 이미 있는 사람을 "신규"로 오판해서
    # 매 실행마다 같은 사람이 중복으로 계속 추가되는 걸 막기 위해, 소문자로
    # 정규화한 키로도 조회할 수 있게 별도 맵을 만든다. 시트에 실제로 저장된
    # 원래 대소문자는 그대로 보존해야 하므로(정규화된 키로 덮어쓰면 안 됨),
    # 조회는 정규화된 키로 하되 갱신은 항상 시트에 있던 원래 soop_id를 쓴다.
    normalized_to_original = {sid.strip().lower(): sid for sid in member_map}

    # members 시트에 없는 신규 등록자는 이제 곧바로 members 시트에 쓰지
    # 않고 new_members(대기) 시트로 보낸다 - 관리자가 검토해서 직접
    # members 시트로 옮기기 전까지는 여기 머무른다. 이미 대기 중인 후보를
    # 정규화된 soop_id 기준으로 미리 알아둬야, 아직 검토 안 끝난 같은
    # 후보가 매 실행마다 new_members 시트에 중복으로 또 쌓이지 않는다.
    pending_map = load_pending_members()
    if pending_map is None:
        # new_members 시트 읽기 실패는 members 시트 읽기 실패보다는 훨씬
        # 가볍다 - 최악의 경우 대기 시트에 중복 후보 행이 하나 더 생기는
        # 정도라 동기화 자체를 중단할 이유는 없다. 다만 조용히 넘어가면
        # 원인 파악이 어려우니 경고는 남긴다.
        print(f"[경고] '{PENDING_SHEET_NAME}' 시트를 읽지 못해 대기 중 후보 중복 체크를 건너뜁니다.", file=sys.stderr)
        pending_map = {}
    already_pending_normalized = {pid.strip().lower() for pid in pending_map}

    api_data = fetch_json(TIERS_URL, label="엘로보드 티어 목록")
    if api_data is None:
        sys.exit(1)

    api_players = flatten_players(api_data)
    if not api_players:
        # 응답은 200이었지만 선수가 한 명도 없는 경우. 이대로 진행하면
        # updates/new_candidates가 전부 비어 "변경 없음"으로 조용히 끝나긴
        # 하지만, 실제로는 API 쪽 사고라 원인을 알 수 있게 실패로 알린다.
        print("[오류] 티어 API에서 선수를 한 명도 얻지 못했습니다 - 시트를 건드리지 않고 중단합니다.",
              file=sys.stderr)
        sys.exit(1)

    today_date_str = kst_now().strftime("%Y-%m-%d")

    updates = {}
    new_candidates = []
    # API가 같은 선수를 여러 티어 그룹(예: 부문/시즌별 그룹)에 중복으로
    # 내려주는 경우, 매번 시트 스냅샷(member_map/normalized_to_original,
    # already_pending_normalized)만 보고 "없는 사람"으로 판단하면 같은
    # 사람이 이번 한 번의 실행 안에서만도 new_candidates에 여러 번 쌓여
    # new_members 시트에 중복 행으로 추가되어버린다. 이번 실행에서 이미
    # 후보로 잡은 soop_id(정규화된 키)를 별도로 기억해뒀다가, 같은 사람이
    # 또 나오면 건너뛴다.
    pending_normalized_ids = set()
    for api_player in api_players:
        soop_id = api_player.get("soop_id")
        if not soop_id or not isinstance(soop_id, str):
            continue
        soop_id = soop_id.strip()
        if not soop_id:
            continue

        normalized_id = soop_id.lower()
        converted_race = RACE_MAP.get(api_player.get("race"), api_player.get("race"))
        team_name = api_player.get("college") or ""

        # API가 주는 soop_id의 대소문자/공백이 시트에 저장된 것과 다를 수 있어서,
        # 정규화된 키로 먼저 조회하고, 찾았으면 시트에 있던 원래 키를 그대로 쓴다
        # (updates 딕셔너리의 키가 write_sheet()에서 실제 행을 찾는 기준이라,
        # 여기서 원래 키를 안 쓰면 기존 행을 못 찾고 또 새 행으로 추가돼버린다).
        original_soop_id = normalized_to_original.get(normalized_id)
        # "이미 등록된 사람인가"는 키가 있느냐로 판단해야지, 그 사람의 필드
        # dict가 truthy냐로 판단하면 안 된다. 모든 칸이 비어 있어 fields가
        # {}가 되는 행이 하나라도 생기면 그 사람은 매 실행마다 "신규"로
        # 오판돼 new_members 시트에 중복으로 계속 쌓인다.
        is_existing_member = original_soop_id is not None
        existing = member_map.get(original_soop_id) or {} if is_existing_member else {}
        if is_existing_member:
            before = (existing.get("nickname"), existing.get("race"), existing.get("tier"), existing.get("team"))
            new_fields = dict(existing)
            if UPDATE_EXISTING_NICKNAME:
                new_fields["nickname"] = api_player.get("name") or existing.get("nickname")
            if UPDATE_EXISTING_RACE:
                new_fields["race"] = converted_race or existing.get("race")
            if UPDATE_EXISTING_TIER:
                new_fields["tier"] = api_player.get("current_tier") or existing.get("tier")
            if UPDATE_EXISTING_TEAM:
                new_fields["team"] = team_name if team_name != "" else existing.get("team")
            
            after = (new_fields.get("nickname"), new_fields.get("race"), new_fields.get("tier"), new_fields.get("team"))
            if before != after:
                updates[original_soop_id] = new_fields
        else:
            if normalized_id in already_pending_normalized or normalized_id in pending_normalized_ids:
                # 이미 new_members 시트에 대기 중이거나(이전 실행에서
                # 추가됨), 이번 실행에서 이미 같은 사람을 후보로 잡았다
                # (API가 같은 선수를 여러 그룹에 중복으로 내려준 경우).
                # 또 추가하면 중복 행이 생기므로 건너뛴다.
                continue
            pending_normalized_ids.add(normalized_id)
            new_candidates.append({
                "id": soop_id,
                "nickname": api_player.get("name"),
                "elo_id": api_player.get("player_id"),
                "gender": "f" if api_player.get("division") == "women" else "m",
                "race": converted_race,
                "tier": api_player.get("current_tier"),
                "team": team_name,
                "source": "sync_members",
                "found_at": today_date_str,
            })

    if len(new_candidates) > MAX_NEW_CANDIDATES_PER_RUN and not args.allow_bulk_new:
        # 한 번에 이 정도가 신규로 잡혔다는 건 거의 확실히 매칭이 깨진 것이다
        # (예: 시트 탭 구조 변경, API가 soop_id 형식을 바꿈). 그대로 쓰면
        # new_members 시트가 쓰레기로 가득 차고 관리자가 수동으로 치워야 한다.
        print(f"[오류] 신규 후보가 {len(new_candidates)}명으로 비정상적으로 많습니다 "
              f"(상한 {MAX_NEW_CANDIDATES_PER_RUN}명) - 매칭이 깨졌을 가능성이 높아 "
              f"시트를 건드리지 않고 중단합니다. 의도한 대량 등록이라면 "
              f"--allow-bulk-new 옵션을 붙여 다시 실행하세요.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        for soop_id, fields in updates.items():
            print(f"  [갱신 예정] {soop_id}: -> {fields}")
        for c in new_candidates:
            print(f"  [신규후보 추가 예정 -> '{PENDING_SHEET_NAME}' 시트] {c['id']} ({c['nickname']})")
        print(f"[dry-run 완료] 갱신 {len(updates)}명, 신규 후보 {len(new_candidates)}명 (구글 시트는 안 건드림)")
        return

    updates_ok = write_sheet(updates, []) if updates else True
    candidates_ok = append_pending_members(new_candidates) if new_candidates else True

    if updates or new_candidates:
        # 실제 쓰기 성공 여부를 그대로 보고한다 - 예전엔 실패해도 "[완료]"만
        # 찍혀서, 로그를 보고도 시트에 아무것도 안 들어간 걸 알 수 없었다.
        if updates_ok and candidates_ok:
            print(f"[완료] 기존 정보 갱신 {len(updates)}명, 신규 후보 {len(new_candidates)}명을 "
                  f"'{PENDING_SHEET_NAME}' 시트에 추가함 (검토 후 members 시트로 옮겨주세요).")
        else:
            print(f"[경고] 시트 쓰기 일부 실패 (갱신 {len(updates)}명: "
                  f"{'성공' if updates_ok else '실패'}, 신규 후보 {len(new_candidates)}명: "
                  f"{'성공' if candidates_ok else '실패'}) - 다음 실행에서 다시 시도됩니다.",
                  file=sys.stderr)
            sys.exit(1)
    else:
        print("[완료] 변경 없음")

if __name__ == "__main__":
    main()
