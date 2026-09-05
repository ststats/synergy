"""
EloBoard의 티어 목록 API(/api/tiers)를 조회해서 구글 시트를 최신 정보로 동기화하는 스크립트.
"""

import sys
import argparse

from _common import (
    fetch_json, is_sheet_ready, load_sheet_members, write_sheet, kst_now
)

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

def normalize_string(val):
    """대소문자를 통일하고 모든 공백을 제거하여 완벽한 비교용 키를 만듭니다."""
    if not val:
        return ""
    return str(val).lower().replace(" ", "").strip()

def normalize_tier(label):
    if isinstance(label, str) and label.endswith("티어"):
        return label[:-len("티어")].strip()
    return label

def flatten_players(api_data: dict) -> list:
    players = []
    for tier_obj in api_data.get("tiers") or []:
        tier_label = normalize_tier(tier_obj.get("label"))
        for player in tier_obj.get("players") or []:
            players.append({**player, "current_tier": tier_label})
    return players

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="파일을 실제로 바꾸지 않고 변경될 내용만 출력")
    args = parser.parse_args(argv)

    if not is_sheet_ready():
        print("[오류] 구글 시트 자격 증명이 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    # 1. 엑셀/시트에서 불러온 멤버 데이터
    member_map = load_sheet_members()
    
    # 2. 불러온 데이터를 바탕으로 강력한 검색 맵 구축
    id_to_original_key = {}
    
    for key, info in member_map.items():
        if not isinstance(info, dict):
            continue
            
        # _common.py가 엑셀 헤더를 그대로 딕셔너리로 반환한다고 가정할 때,
        # 'SOOP ID'라는 정확한 헤더 이름까지 포함해서 스캔합니다.
        # 만약 key 자체가 ID로 잡혔다면 key도 후보에 넣습니다.
        possible_ids = [
            info.get("SOOP ID"), # 엑셀 헤더 정확히 매칭
            info.get("soop_id"),
            info.get("id"),
            str(key)
        ]
        
        for p_id in possible_ids:
            if p_id:
                id_to_original_key[normalize_string(p_id)] = key
                break # 유효한 ID를 하나 찾았으면 다음 멤버로 넘어감

    api_data = fetch_json(TIERS_URL, label="엘로보드 티어 목록")
    if api_data is None:
        sys.exit(1)

    api_players = flatten_players(api_data)
    today_date_str = kst_now().strftime("%Y-%m-%d")

    updates = {}
    new_candidates = []
    pending_normalized_ids = set()

    for api_player in api_players:
        soop_id = api_player.get("soop_id")
        nickname = api_player.get("name")
        if not soop_id:
            continue

        norm_id = normalize_string(soop_id)
        converted_race = RACE_MAP.get(api_player.get("race"), api_player.get("race"))
        team_name = api_player.get("college") or ""

        # 기존 멤버인지 확인
        original_key = id_to_original_key.get(norm_id)
        existing = member_map.get(original_key) if original_key else None
        
        if existing:
            # 업데이트 로직 (변경 사항 체크)
            before = (existing.get("nickname") or existing.get("이름"), 
                      existing.get("race") or existing.get("종족"), 
                      existing.get("tier") or existing.get("티어"), 
                      existing.get("team") or existing.get("소속"))
                      
            new_fields = dict(existing)
            if UPDATE_EXISTING_NICKNAME:
                new_fields["nickname"] = nickname or before[0]
            if UPDATE_EXISTING_RACE:
                new_fields["race"] = converted_race or before[1]
            if UPDATE_EXISTING_TIER:
                new_fields["tier"] = api_player.get("current_tier") or before[2]
            if UPDATE_EXISTING_TEAM:
                new_fields["team"] = team_name if team_name != "" else before[3]
            
            after = (new_fields.get("nickname"), new_fields.get("race"), new_fields.get("tier"), new_fields.get("team"))
            if before != after:
                updates[original_key] = new_fields
        else:
            # 완벽히 없는 새로운 인원일 때만 추가
            if norm_id in pending_normalized_ids:
                continue 
            
            pending_normalized_ids.add(norm_id)
            new_candidates.append({
                "id": soop_id,
                "nickname": nickname,
                "elo_id": api_player.get("player_id"),
                "gender": "f" if api_player.get("division") == "women" else "m",
                "race": converted_race,
                "tier": api_player.get("current_tier"),
                "team": team_name,
                "source": "sync_members",
                "found_at": today_date_str,
            })

    if args.dry_run:
        for k, fields in updates.items():
            print(f"  [갱신 예정] {k}: -> {fields}")
        for c in new_candidates:
            print(f"  [신규멤버 추가 예정 -> members 시트] {c['id']} ({c['nickname']})")
        print(f"[dry-run 완료] 갱신 {len(updates)}명, 신규 추가 {len(new_candidates)}명 (구글 시트는 안 건드림)")
        return

    if updates or new_candidates:
        write_sheet(updates, new_candidates)
        print(f"[완료] 기존 정보 갱신 {len(updates)}명, 신규 멤버 {len(new_candidates)}명을 메인 시트에 바로 추가함.")
    else:
        print("[완료] 변경 없음")

if __name__ == "__main__":
    main()
