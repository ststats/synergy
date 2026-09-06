"""
synergy 프로젝트의 풍고(poonggo.com) 데이터 fetch 모듈.

여기는 풍고 API에서 별풍선/방송시간/누적시청자를 가져오는 순수 함수만 있습니다.

HTTP 요청/재시도는 _common.fetch_json()에 맡긴다 - fetch_eloboard_data.py와 동일한
타임아웃/재시도 횟수/백오프 간격을 쓰도록 통일했다. (예전에는 여기서 urllib으로
직접 재시도 루프를 돌렸는데, 재시도 사이에 대기(backoff)가 아예 없어서 실패하자마자
곧바로 재요청하는 상태였다 - fetch_json()으로 옮기면서 그 문제도 같이 없어진다.)
"""

import sys

from _common import to_int as _to_int, fetch_json

POONGGO_MONTHLY_URL = "https://poonggo.com/api/monthly"
IDS_PER_REQUEST = 300


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

    for chunk in _chunked(ids, IDS_PER_REQUEST):
        ids_param = ",".join(chunk)
        url = f"{POONGGO_MONTHLY_URL}?date={date_str}&ids={ids_param}"
        label = f"풍고({date_str}, {len(chunk)}명분)"

        parsed = fetch_json(url, label=label)
        if parsed is None:
            print(f"[오류] {label} 조회 실패", file=sys.stderr)
            return None

        entries = parsed if isinstance(parsed, list) else parsed.get("data", parsed.get("list", []))
        for entry in entries:
            member_id = entry.get("id")
            if member_id:
                data_by_id[member_id] = {
                    "balloons": _to_int(entry.get("amt")),
                    "broadcast_seconds": _to_int(entry.get("broadTime")),
                    "cumulative_viewers": _to_int(entry.get("cview")),
                }
    return data_by_id
