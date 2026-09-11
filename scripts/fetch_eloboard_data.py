"""
synergy 프로젝트의 엘로보드(ELO) 스폰전적 fetch 모듈.

이전에는 ZenRows(안티봇 우회 프록시) + JS 주입으로 게시판 HTML을 긁어서
afreecatv 핸들을 정규식으로 추출하는 방식이었으나, EloBoard가 공식 API
(eloboard.co.kr/api/matches)를 제공하면서 player_id(=members.json의
elo_id) 기준으로 직접 조회하는 방식으로 교체되었다. 매칭 키가
"afreecatv id" -> "elo_id(숫자)"로 바뀐 만큼, 호출부인 update_data.py도
elo_id <-> id 매핑 테이블을 거쳐서 이 함수의 반환값을 소비한다.

날짜 포맷은 API의 played_on 필드와 문자열 비교가 되도록 "YYYY-MM-DD"
(ISO, 대시 포함)를 그대로 쓴다 - get_month_date_range()는 _common.py로
옮겨졌다(포맷이 바뀌었으니 update_data.py의 수동 날짜 조합 로직과 함께
한 곳에서 관리하기 위함).

HTTP 요청/재시도는 _common.fetch_json()에 맡긴다 - fetch_poonggo_data.py와
동일한 타임아웃/재시도 횟수/백오프 간격을 쓰도록 통일했다(자체 재시도 루프를
따로 두지 않는다).

이 모듈은 순수하게 "가져오기만" 한다 - 구글 시트에 신규 elo_id를
자동 등록하는 로직(예전엔 여기서 aggregate_period_data() 끝에 자동으로
불렀었다)은 update_data.py(오케스트레이터)로 옮겼다. 수집 모듈이 파일
쓰기라는 부수 효과(side effect)까지 갖고 있으면, "이 함수를 부르면 정확히
뭐가 바뀌는지"를 호출부만 봐서는 알 수 없게 된다 - 오케스트레이터가 수집
결과를 받아서 뭘 할지 명시적으로 결정하는 게 책임이 더 명확하다.
"""

import time
import sys

from _common import fetch_json
from collections import defaultdict

PAGE_LIMIT = 200
SLEEP_BETWEEN_PAGES_SEC = 0.5
# 이론상 API 응답이 정렬 가정과 다르게 오거나(더 이상 과거로 못 내려가는 상태)
# 종료 조건이 어긋나면 while True가 끝없이 돌면서 워크플로우 전체 시간(25분
# 타임아웃)을 다 잡아먹을 수 있다 - 그러면 그날은 별풍선 수집도, 페이지 생성도,
# 커밋도 전혀 안 된다. 충분히 넉넉하지만(200명 x 2000페이지 = 40만 건) 무한
# 루프는 막아주는 상한선을 둔다.
MAX_PAGES = 2000
# 오래된 매치를 "한 건" 만났다고 바로 탐색을 끝내지 않고, 연속으로 몇 "페이지
# 전체"가 다 기간보다 오래됐을 때만 진짜로 끝낸다(아래 aggregate_period_data
# 문서 참고 - 늦게 뒤섞여 들어온 과거 데이터에 대한 안전판). 3페이지(최대
# 600건) 정도의 완충 구간을 두면, 어지간한 규모의 뒤늦은 데이터 유입도
# 흡수하면서 실제로 기간을 다 지나쳤을 때는 여전히 합리적으로 빨리 멈춘다.
OLD_PAGE_STREAK_TO_STOP = 3
# MAX_PAGES(2000)는 "무한 루프"는 막아주지만 시간은 전혀 막아주지 못한다 -
# 페이지당 sleep 0.5초 + 요청 시간만 쳐도 2000페이지는 20분이 훌쩍 넘고,
# update_data.py는 이걸 달 확정까지 포함해 여러 번 부를 수 있어서 워크플로우
# 전체 타임아웃(25분)을 그대로 태워버린다. 그러면 그날은 커밋 자체가 안 나서
# 별풍선 수집 결과까지 통째로 버려진다. 한 번의 수집이 쓸 수 있는 실제
# "벽시계 시간" 상한을 따로 둬서, 오래 걸릴 것 같으면 워크플로우를 죽이는
# 대신 이 수집만 깔끔히 실패 처리하고 나머지 단계에 시간을 넘겨준다.
MAX_ELAPSED_SEC = 420


def aggregate_period_data(start_date: str, end_date: str) -> list:
    """EloBoard API가 제공하는 player_id(elo_id)를 직접 사용하여
    지정된 기간(YYYY-MM-DD ~ YYYY-MM-DD)의 스폰전적을 합산한다.

    반환하는 각 항목의 "id"는 afreecatv 핸들이 아니라 elo_id(숫자를
    문자열화한 것)이다 - 호출부에서 members.json의 elo_id와 매칭해야 한다.

    API가 대체로 최신 -> 과거 순으로 정렬해서 내려준다고 가정하지만, 완벽하게
    그 순서만 믿지는 않는다 - EloBoard 쪽에서 늦게 발견되거나 복구된 과거
    데이터가, 실제 played_on 날짜 순서가 아니라 "이제 막 등록된 순서"로
    최근 데이터 사이에 섞여 나타날 수 있다(실제로 8월 데이터가 한동안
    누락됐다가 나중에 올라온 사례가 있었다). 그래서 start_date보다 이른
    매치를 "한 건" 만났다고 바로 중단하지 않고, 연속으로 여러 "페이지 전체"가
    다 start_date보다 오래됐을 때만(OLD_PAGE_STREAK_TO_STOP 참고) 종료한다 -
    그 사이 섞여 들어온 예외적인 옛날 기록 몇 개는 그냥 건너뛰고 계속
    진행하되, 실제로 그 기간을 다 지나쳤다는 확신이 들 때만 멈춘다.

    페이지 요청이 fetch_json()의 재시도를 다 소진해도 실패하면, 지금까지 모은
    데이터가 불완전하다는 걸 명확히 경고로 남기고 빈 리스트를 반환한다(부분
    데이터를 "정상 수집 결과"인 것처럼 조용히 반환하지 않기 위함 - 호출부인
    update_data.py는 빈 리스트를 "이번엔 실패, 기존 값 유지"로 처리한다).
    한 참가자 레코드가 예상과 다른 형태여도 그 레코드만 건너뛰고 나머지는
    계속 집계한다.
    """
    offset = 0
    page_count = 0
    combined_dict = defaultdict(lambda: {"id": None, "sponsor_wins": 0, "sponsor_losses": 0})
    consecutive_fully_old_pages = 0
    started_at = time.monotonic()
    skipped_records = 0

    print(f"[요청] {start_date} ~ {end_date} 기간 전적 수집 시작...")

    while True:
        page_count += 1
        if time.monotonic() - started_at > MAX_ELAPSED_SEC:
            print(f"[오류] 스폰전적 수집이 {MAX_ELAPSED_SEC}초를 넘겨 중단합니다 "
                  f"(offset={offset}, {page_count}페이지째). 지금까지 모은 데이터는 "
                  f"불완전하므로 이번 수집 전체를 실패 처리합니다.", file=sys.stderr)
            return []
        if page_count > MAX_PAGES:
            print(f"[오류] {MAX_PAGES}페이지를 넘겨도 종료 조건에 도달 못 함 - "
                  f"정렬 가정이 깨졌거나 API 문제로 보입니다. 지금까지 모은 데이터는 "
                  f"불완전하므로 이번 수집 전체를 실패 처리합니다.", file=sys.stderr)
            return []

        url = f"https://eloboard.co.kr/api/matches?limit={PAGE_LIMIT}&offset={offset}"
        matches = fetch_json(url, label=f"엘로보드(offset={offset})")
        if matches is None:
            print(f"[오류] offset={offset} 페이지를 재시도해도 가져오지 못함 - "
                  f"지금까지 모은 데이터는 불완전하므로 이번 수집 전체를 실패 처리합니다.", file=sys.stderr)
            return []

        if not isinstance(matches, list):
            # API가 평소의 리스트가 아니라 dict(예: {"error": ...})를 돌려주면
            # 아래 for 루프가 "키 문자열"을 매치로 착각해서 엉뚱하게 돌다가
            # AttributeError로 터진다. 형식이 다르면 그냥 실패로 본다.
            print(f"[오류] offset={offset} 응답이 리스트가 아닙니다({type(matches).__name__}) - "
                  f"이번 수집 전체를 실패 처리합니다.", file=sys.stderr)
            return []

        if not matches:
            break

        page_has_in_range_match = False
        for match in matches:
            if not isinstance(match, dict):
                skipped_records += 1
                continue
            played_on = match.get("played_on")
            if not isinstance(played_on, str) or not played_on:
                # played_on이 null이거나 숫자/누락으로 오면 아래 문자열 비교
                # (played_on > end_date)에서 TypeError가 나면서 수집 전체가
                # 죽는다 - 그 달 스폰전적이 통째로 날아간다. 레코드 하나
                # 때문에 전부를 잃지 않도록 그 레코드만 건너뛴다(값이 빈
                # 문자열일 때 기존 코드가 하던 동작과 결과적으로 동일하다).
                skipped_records += 1
                continue

            if played_on > end_date:
                # 아직 기간 이후(더 최신) 데이터 - 계속 넘어간다
                continue
            elif played_on < start_date:
                # 이 매치 하나는 기간보다 과거지만, 같은 페이지 안에 기간
                # 안쪽 매치가 더 있을 수도 있으니(늦게 섞여 들어온 경우) 이
                # 레코드만 건너뛰고 페이지 끝까지는 마저 확인한다.
                continue
            else:
                page_has_in_range_match = True
                participants = match.get("participants") or []
                if not isinstance(participants, list):
                    skipped_records += 1
                    continue
                for p in participants:
                    if not isinstance(p, dict):
                        skipped_records += 1
                        continue
                    try:
                        raw_player_id = p["player_id"]
                        result = p["result"]
                        if raw_player_id is None:
                            # str(None) == "None"이라 예외 없이 그냥 통과돼버려서,
                            # 이 값이 나중에 진짜 elo_id인 것처럼 끝까지 흘러가다가
                            # int("None") 하는 시점에서야 터진다(실제로 이렇게
                            # 터진 적이 있었다). player_id가 null인 참가자
                            # 레코드는 처음부터 명시적으로 걸러낸다.
                            raise ValueError("player_id가 null")
                        elo_id = str(raw_player_id)
                    except (KeyError, TypeError, ValueError) as e:
                        print(f"[경고] 형식이 예상과 다른 참가자 레코드 건너뜀: {e} ({p!r})", file=sys.stderr)
                        continue

                    if combined_dict[elo_id]["id"] is None:
                        combined_dict[elo_id]["id"] = elo_id

                    if result == "win":
                        combined_dict[elo_id]["sponsor_wins"] += 1
                    else:
                        combined_dict[elo_id]["sponsor_losses"] += 1

        if page_has_in_range_match:
            consecutive_fully_old_pages = 0
        else:
            # 이 페이지엔 기간 안쪽 매치가 하나도 없었다 - 다만 아직은
            # "늦게 섞여 들어온 예외" 가능성을 배제 못 하므로, 연속으로 몇
            # 페이지가 더 이렇게 나와야 진짜로 기간을 다 지나쳤다고 판단한다.
            consecutive_fully_old_pages += 1
            if consecutive_fully_old_pages >= OLD_PAGE_STREAK_TO_STOP:
                print(f"[완료] {OLD_PAGE_STREAK_TO_STOP}페이지 연속으로 {start_date} 이전 데이터만 "
                      f"나와 탐색을 종료합니다.")
                break

        offset += PAGE_LIMIT
        time.sleep(SLEEP_BETWEEN_PAGES_SEC)

    if skipped_records:
        # 조용히 넘어가면 "왜 특정 인원 전적이 비어 있지?"를 나중에 추적할
        # 방법이 없다. 몇 건을 왜 버렸는지 한 줄이라도 남긴다.
        print(f"[경고] 형식이 예상과 다른 레코드 {skipped_records}건을 건너뛰었습니다.", file=sys.stderr)

    result = [v for v in combined_dict.values() if v["id"] is not None]
    print(f"[완료] {start_date} ~ {end_date} 스폰전적 {len(result)}명분 수집 "
          f"({page_count}페이지, {time.monotonic() - started_at:.1f}초)")
    return result
