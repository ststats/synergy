"""
synergy 프로젝트의 풍고(poonggo.com) 데이터 fetch 모듈.

여기는 풍고 API에서 별풍선/방송시간/누적시청자를 가져오는 순수 함수만 있습니다.

HTTP 요청/재시도는 _common.fetch_json()에 맡긴다 - fetch_eloboard_data.py와 동일한
타임아웃/재시도 횟수/백오프 간격을 쓰도록 통일했다. (예전에는 여기서 urllib으로
직접 재시도 루프를 돌렸는데, 재시도 사이에 대기(backoff)가 아예 없어서 실패하자마자
곧바로 재요청하는 상태였다 - fetch_json()으로 옮기면서 그 문제도 같이 없어진다.)
"""

import sys
import time
from urllib.parse import quote

from _common import to_int as _to_int, fetch_json

POONGGO_MONTHLY_URL = "https://poonggo.com/api/monthly"
IDS_PER_REQUEST = 300
# 여러 묶음을 연속으로 쏘면 상대 쪽에서 rate limit에 걸릴 수 있으므로,
# 묶음이 2개 이상일 때만 사이에 짧게 쉰다(대부분의 로스터는 1묶음이라
# 실제로는 아무 지연도 추가되지 않는다).
SLEEP_BETWEEN_CHUNKS_SEC = 0.5


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_poonggo_monthly(year: int, month: int, ids: list):
    """지정한 연/월 기준으로 ids에 해당하는 사람들의 별풍선/방송시간/누적시청자를
    가져온다. 실패하면 None을 반환한다(빈 dict {}는 "성공했지만 대상이 없음"과
    구분하기 위해)."""
    if not ids:
        return {}
    date_str = f"{year:04d}-{month:02d}-01"
    data_by_id = {}
    # id에 &, #, 공백 같은 게 섞이면 쿼리스트링이 통째로 깨져서 그 묶음 전체가
    # 엉뚱하게 조회된다(조용히 0명으로 돌아오거나, 최악의 경우 다른 파라미터를
    # 주입하는 모양이 된다). 구분자인 쉼표는 살리고 나머지만 인코딩한다.
    chunks = list(_chunked([str(i) for i in ids], IDS_PER_REQUEST))

    for chunk_idx, chunk in enumerate(chunks):
        if chunk_idx > 0:
            time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)
        ids_param = quote(",".join(chunk), safe=",")
        url = f"{POONGGO_MONTHLY_URL}?date={date_str}&ids={ids_param}"
        label = f"풍고({date_str}, {len(chunk)}명분)"

        parsed = fetch_json(url, label=label)
        if parsed is None:
            print(f"[오류] {label} 조회 실패", file=sys.stderr)
            return None

        entries = _extract_entries(parsed)
        if entries is None:
            # 형식이 아예 예상 밖이면 "0명 조회됨"으로 착각하지 않는다 -
            # 그렇게 되면 호출부가 전원의 별풍선을 0으로 덮어써버린다.
            print(f"[오류] {label} 응답 형식이 예상과 다릅니다({type(parsed).__name__})", file=sys.stderr)
            return None

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            member_id = entry.get("id")
            if member_id:
                data_by_id[str(member_id).strip()] = {
                    "balloons": _to_int(entry.get("amt")),
                    "broadcast_seconds": _to_int(entry.get("broadTime")),
                    "cumulative_viewers": _to_int(entry.get("cview")),
                }
    return data_by_id


def _extract_entries(parsed):
    """응답에서 레코드 리스트를 꺼낸다. 리스트를 못 찾으면 None(형식 오류).

    예전에는 `parsed.get("data", parsed.get("list", []))`였는데, "data" 키가
    있지만 값이 null인 응답에서는 None이 그대로 나와 `for entry in None`으로
    TypeError가 났다. 또 parsed가 리스트도 dict도 아닌 경우(문자열/숫자)엔
    .get에서 AttributeError가 났다."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("data", "list", "result", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        # dict인데 알려진 키가 전부 없으면 "결과 없음"이 맞을 수도 있으나,
        # 판단할 근거가 없으므로 형식 오류로 보고 호출부가 실패 처리하게 한다.
        return None
    return None
