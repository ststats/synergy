"""
synergy 프로젝트의 스크립트들이 공통으로 쓰는 유틸리티.
"""

import sys
import os
import json
import time
import calendar
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"
USER_AGENT = "ststats-bot/1.0 (+https://ststats.github.io)"
HTTP_TIMEOUT_SEC = 30
HTTP_MAX_RETRIES = 3
HTTP_RETRY_BACKOFF_SEC = 3
# 4xx 중에서도 "잠시 후 다시 하면 될 수도 있는" 것들만 재시도한다. 400/401/
# 403/404 같은 건 몇 번을 더 보내도 똑같은 응답이 올 뿐이라, 재시도는 실패
# 확정까지의 시간만 늘리고(1회당 backoff만큼) 상대 서버에 부하만 더 준다.
HTTP_RETRYABLE_CLIENT_STATUSES = {408, 425, 429}

KST = timezone(timedelta(hours=9), "KST")

def kst_now() -> datetime:
    """한국 시간(KST) 기준 현재 시각.

    예전 구현은 `datetime.now(timezone.utc) + timedelta(hours=9)`였는데, 이건
    "표시되는 숫자는 KST인데 tzinfo는 UTC라고 주장하는" 어긋난 datetime을
    만든다. strftime/year/month처럼 숫자만 쓰는 지금 호출부에서는 결과가
    같지만, 누군가 나중에 .timestamp()를 쓰거나 다른 aware datetime과
    비교하는 순간 조용히 9시간이 틀어진다. tzinfo를 실제 KST로 바로잡아
    두면 그 지뢰가 사라지고, 기존 호출부의 출력값은 100% 동일하다."""
    return datetime.now(KST)

def to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0

def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def get_month_date_range(dt: datetime) -> tuple:
    last_day = last_day_of_month(dt.year, dt.month)
    return dt.strftime("%Y-%m-01"), dt.strftime(f"%Y-%m-{last_day:02d}")

def fetch_json(url: str, *, method: str = "GET", params=None, data=None, headers=None,
                label: str = "", max_retries: int = HTTP_MAX_RETRIES,
                timeout: int = HTTP_TIMEOUT_SEC, backoff: int = HTTP_RETRY_BACKOFF_SEC):
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    prefix = f"{label} " if label else ""

    for attempt in range(1, max_retries + 1):
        retry_after = None
        try:
            response = requests.request(
                method, url, params=params, data=data, headers=req_headers, timeout=timeout
            )
            if response.status_code == 200:
                return response.json()

            print(f"[경고] {prefix}HTTP {response.status_code} 응답 ({attempt}/{max_retries})", file=sys.stderr)
            status = response.status_code
            if 400 <= status < 500 and status not in HTTP_RETRYABLE_CLIENT_STATUSES:
                # 재시도해도 결과가 안 바뀌는 클라이언트 오류 - 즉시 포기한다.
                print(f"[오류] {prefix}HTTP {status}는 재시도해도 동일하므로 즉시 실패 처리합니다.", file=sys.stderr)
                return None
            if status == 429:
                # 서버가 "이만큼 기다려라"라고 알려주면 그 값을 존중한다 -
                # 안 그러면 우리 고정 backoff가 더 짧을 때 429를 계속
                # 유발하면서 쓸데없이 차단만 깊어진다.
                raw = response.headers.get("Retry-After")
                if raw:
                    try:
                        retry_after = min(60, max(0, int(float(raw))))
                    except (ValueError, TypeError):
                        retry_after = None
        except requests.RequestException as e:
            print(f"[경고] {prefix}요청 실패: {e} ({attempt}/{max_retries})", file=sys.stderr)
        except ValueError as e:
            print(f"[경고] {prefix}응답 파싱 실패: {e} ({attempt}/{max_retries})", file=sys.stderr)

        if attempt < max_retries:
            # 고정 간격이 아니라 지수 백오프(3s -> 6s -> 12s, 30s 상한)를 쓴다.
            # 상대 서버가 일시적으로 과부하일 때 같은 간격으로 계속 두드리면
            # 회복을 방해할 뿐이다.
            wait = retry_after if retry_after is not None else min(30, backoff * (2 ** (attempt - 1)))
            time.sleep(wait)
    return None

def atomic_write_json(path: Path, data, **json_kwargs) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_kwargs.setdefault("ensure_ascii", False)
    json_kwargs.setdefault("indent", 2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **json_kwargs)
            # os.replace는 "이름 바꾸기"만 원자적으로 보장할 뿐, 파일 내용이
            # 실제로 디스크에 내려갔는지는 보장하지 않는다 - 러너가 갑자기
            # 죽으면 이름은 바뀌었는데 내용은 0바이트인 파일이 남을 수 있다.
            # flush+fsync로 내용을 먼저 확정한 뒤 이름을 바꾼다.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def safe_read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[경고] {path}를 읽을 수 없어 기본값으로 대체합니다: {e}", file=sys.stderr)
        return default

REQUIRED_MEMBER_STRING_FIELDS = ("id", "nickname")
VALID_GENDERS = {"m", "f", None}

def validate_and_clean_members(members: list) -> list:
    """members.json(구글 시트에서 파생된 것)의 각 레코드가 최소한의 스키마를
    만족하는지 검사한다. id/nickname이 비어있는 레코드는 경고 로그를 남기고
    걸러낸다 - 로그 없이 조용히 건너뛰면, 구글 시트에서 관리자가 실수로 셀을
    지웠을 때 그 사람이 사이트에서 아무 흔적 없이 사라져버려서 원인을 찾기
    매우 어려워진다."""
    cleaned = []
    for idx, m in enumerate(members):
        if not isinstance(m, dict):
            print(f"[경고] members[{idx}]가 올바른 형식이 아니라 건너뜁니다: {m!r}", file=sys.stderr)
            continue
        member_id = m.get("id")
        nickname = m.get("nickname")
        if not member_id or not nickname:
            print(f"[경고] members[{idx}]에 SOOP ID 또는 이름이 비어있어 건너뜁니다"
                  f"(구글 시트에서 셀이 실수로 지워지지 않았는지 확인하세요): {m!r}", file=sys.stderr)
            continue
        m = dict(m)
        # id 앞뒤 공백은 반드시 털어낸다 - 이 값이 풍고 조회 키이자
        # 아카이브/프론트엔드의 매칭 키라, 공백 하나 때문에 그 사람의
        # 별풍선이 통째로 0으로 보이는 식의 조용한 오류가 난다.
        m["id"] = str(member_id).strip()
        if not m["id"]:
            print(f"[경고] members[{idx}]의 SOOP ID가 공백뿐이라 건너뜁니다: {m!r}", file=sys.stderr)
            continue
        elo_id = m.get("elo_id")
        if elo_id is not None:
            # load_sheet_members()는 "6199.0" 같은 값을 int(float(...))로
            # 관대하게 받아들이는데 여기만 int()를 바로 써서, 같은 값이
            # 경로에 따라 6199가 되기도 하고 None이 되기도 했다. None이
            # 되면 그 사람의 스폰전적이 조용히 통째로 사라진다.
            # 판단 기준을 한 곳(normalize_elo_id)으로 통일한다.
            normalized = normalize_elo_id(elo_id)
            try:
                m["elo_id"] = int(normalized) if normalized else None
            except (ValueError, TypeError):
                print(f"[경고] members[{idx}]({m['id']})의 elo_id '{elo_id}'를 숫자로 "
                      f"해석할 수 없어 비워둡니다.", file=sys.stderr)
                m["elo_id"] = None
        cleaned.append(m)
    return cleaned

# ---------------------------------------------------------------------------
# 구글 시트 관련 공용 헬퍼 (최적화 및 안정화 적용)
# ---------------------------------------------------------------------------

SHEET_NAME = "members"
# 아직 검증되지 않은 "신규 후보"(스폰전적에만 등장하는 미상 elo_id,
# 티어 API에는 있지만 members 시트엔 없는 신규 등록자)는 곧바로
# members 시트에 쓰지 않고 이 별도 시트에 쌓아둔다. members 시트는
# 여전히 "사람이 검토를 마친" 로스터만 담아야 정확성이 유지되기
# 때문에, 자동 발견된 후보는 관리자가 검토해서 직접 members 시트로
# 옮기기 전까지는 여기 머무른다(자동 승격 로직은 의도적으로 없음).
PENDING_SHEET_NAME = "new_members"
PENDING_SHEET_COLUMNS = 9  # 닉네임, id, elo_id, 성별, 종족, 티어, 팀, 출처, 발견일
SHEET_GENDER_MAP = {"남자": "m", "여자": "f"}
SHEET_GENDER_MAP_REVERSE = {"m": "남자", "f": "여자"}
SHEET_PLACEHOLDER_VALUES = {"체크", "todo", "?", "미정", "", "null", "none", "n/a", "na"}

# API 클라이언트 전역 캐싱 (인증 오버헤드 최소화)
_gspread_client = None
# 스프레드시트 핸들도 캐싱한다 - open_by_key()는 매번 실제 API 호출이라,
# 캐싱하지 않으면 한 번 실행에 시트를 4~5번 여닫는 동안 그만큼의 읽기
# 쿼터(사용자당 분당 60회)를 공짜로 태워버린다.
_spreadsheet = None

# 구글 시트 API는 일시적인 500/503/429를 꽤 자주 돌려준다. 한 번 실패했다고
# 그날 동기화를 통째로 포기하는 건 과하므로, 짧게 몇 번만 다시 시도한다.
SHEET_MAX_RETRIES = 3
SHEET_RETRY_BACKOFF_SEC = 5


def _is_retryable_sheet_error(exc: Exception) -> bool:
    """gspread.APIError 중 잠시 뒤 다시 하면 될 가능성이 있는 것만 True.
    (gspread를 지연 import하는 설계를 깨지 않으려고 타입 대신 응답 코드를
    덕타이핑으로 확인한다 - gspread가 설치 안 된 환경에서도 이 모듈은
    import만으로는 절대 죽지 않아야 한다.)"""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        return False
    return status in (429, 500, 502, 503, 504)


def _sheet_call(fn, *args, what: str = "구글 시트 작업", **kwargs):
    """시트 API 호출을 짧은 재시도로 감싼다. 재시도를 다 쓰면 마지막 예외를
    그대로 올려서, 기존 호출부의 try/except 처리 방식(읽기 실패=None,
    쓰기 실패=건너뜀)이 그대로 동작하게 둔다."""
    for attempt in range(1, SHEET_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt >= SHEET_MAX_RETRIES or not _is_retryable_sheet_error(e):
                raise
            wait = SHEET_RETRY_BACKOFF_SEC * attempt
            print(f"[경고] {what} 일시 실패({attempt}/{SHEET_MAX_RETRIES}), {wait}초 후 재시도: {e}",
                  file=sys.stderr)
            time.sleep(wait)

def is_sheet_ready() -> bool:
    return bool(os.environ.get("GOOGLE_CREDENTIALS_JSON")) and bool(os.environ.get("GOOGLE_SHEET_ID"))

def get_gspread_client():
    """gspread/google-auth를 함수 안에서 지연 import한다 - fetch_poonggo_data.py,
    fetch_eloboard_data.py, generate_pages.py, subset_font.py처럼 구글시트를
    전혀 안 쓰는 스크립트가 _common을 import하는 것만으로 gspread가 없다고
    크래시하는 일이 없게 하기 위함이다(워크플로우는 requirements.txt를
    한꺼번에 설치하니 실제로 크래시가 나진 않지만, 불필요하게 강한 결합을
    만들지 않는 게 좋다)."""
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None

    import gspread
    from google.oauth2.service_account import Credentials

    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError as e:
        # 시크릿이 깨진 채로 들어오면 여기서 나는 JSONDecodeError가 그대로
        # 위로 올라가 스크립트를 죽인다. 원인을 알 수 있게 명시적으로
        # 말해주고(단, 자격증명 내용 자체는 절대 로그에 남기지 않는다)
        # "시트 사용 불가" 상태로 취급하게 None을 돌려준다.
        print(f"[오류] GOOGLE_CREDENTIALS_JSON이 올바른 JSON이 아닙니다: {e}", file=sys.stderr)
        return None

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _gspread_client = gspread.authorize(creds)
    except Exception as e:
        print(f"[오류] 구글 서비스 계정 인증에 실패했습니다: {e}", file=sys.stderr)
        return None
    return _gspread_client

def get_worksheet(sheet_name: str = SHEET_NAME):
    global _spreadsheet
    gc = get_gspread_client()
    if not gc: return None
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id: return None
    if _spreadsheet is None:
        _spreadsheet = _sheet_call(gc.open_by_key, sheet_id, what="스프레드시트 열기")
    return _sheet_call(_spreadsheet.worksheet, sheet_name, what=f"'{sheet_name}' 탭 열기")

def sheet_clean(value):
    if isinstance(value, str) and value.strip().lower() in SHEET_PLACEHOLDER_VALUES:
        return None
    return value if value != "" else None

def sheet_format_date(value):
    value = sheet_clean(value)
    if not value:
        return None
    return str(value).strip()

def sheet_parse_date_for_write(value):
    """시트에 다시 써넣을 날짜 문자열을 만든다.

    예전 구현은 "%Y-%m-%d"로 파싱이 안 되는 값을 전부 ""로 바꿔서 돌려줬다.
    그런데 이 반환값은 write_sheet()에서 그대로 셀에 덮어써지기 때문에,
    관리자가 생년월일을 "2000.01.01"이나 "1999/12/31"처럼 입력해 둔 사람은
    (티어/팀이 한 번이라도 갱신되는 순간) 생년월일 셀이 통째로 지워졌다.
    수집 스크립트가 사람이 입력한 데이터를 지우는 건 어떤 경우에도 정당화가
    안 되므로, 이제는 흔한 표기들을 ISO로 정규화해보고, 그래도 모르는
    형식이면 원본 문자열을 그대로 보존한다(지우지 않는다)."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime(DATE_FORMAT)
        except ValueError:
            continue
    print(f"[경고] 날짜 형식을 알 수 없어 원본을 그대로 보존합니다: {text!r}", file=sys.stderr)
    return text

def load_sheet_members():
    """반환값은 성공하면 dict(사람이 진짜 0명이어도 {} - 정상)이고,
    시트 읽기 자체가 실패하면 None이다. 이 둘을 구분해야 하는 이유:
    실패를 빈 dict({})와 똑같이 취급하면 호출부(특히 sync_members.py)가
    "기존 회원이 0명"이라고 착각해서, 이미 등록된 수백 명을 전부 "신규"로
    오판해 new_members 시트에 통째로 다시 밀어넣는 사고로 이어진다(실제로
    탭 이름 불일치/권한 문제 등으로 이런 일이 있었다)."""
    if not is_sheet_ready(): return {}
    try:
        ws = get_worksheet()
        if ws is None:
            print("[오류] 구글 시트 워크시트를 열지 못했습니다.", file=sys.stderr)
            return None
        all_values = _sheet_call(ws.get_all_values, what=f"'{SHEET_NAME}' 시트 읽기")
    except Exception as e:
        print(f"[오류] 구글 시트를 읽어오는 중 에러 발생: {e}", file=sys.stderr)
        return None

    if not all_values or len(all_values) < 2:
        return {}

    rows = {}
    for row_idx, row in enumerate(all_values[1:], start=2):
        row = (list(row) + [''] * 10)[:10]
        nickname, soop_id, elo_id, birthdate, gender, race, tier, team, role, updated_at = row

        if not nickname or not soop_id:
            continue

        soop_id = str(soop_id).strip()
        if not soop_id:
            continue
        if soop_id in rows:
            # 같은 SOOP ID가 두 행에 있으면 뒤 행이 앞 행을 조용히 덮어쓴다 -
            # 관리자가 실수로 같은 사람을 두 번 넣은 경우 어느 쪽 정보가
            # 반영되는지 아무도 모르게 되므로 최소한 경고는 남긴다.
            print(f"[경고] {row_idx}행: SOOP ID '{soop_id}'가 시트에 중복으로 존재합니다 "
                  f"(뒤 행 기준으로 덮어씁니다).", file=sys.stderr)
        tier = sheet_clean(tier)
        gender_raw = str(gender).strip()

        try:
            elo_id_str = str(elo_id).strip()
            # 시트 셀 서식/입력 방식에 따라 정수 열이 "6199.0"처럼 소수점
            # 붙은 형태로 올 수 있다(예: 셀이 "숫자" 형식으로 지정된 경우) -
            # int()를 바로 쓰면 이런 값에서 예외가 나서 조용히 None이
            # 되어버리므로, float를 한 번 거쳐서 정수부만 취한다.
            # OverflowError는 "1e999"(inf)처럼 터무니없는 값에서 나는데,
            # 이걸 안 잡으면 시트 셀 오타 하나가 스크립트 전체를 죽인다.
            elo_id_int = int(float(elo_id_str)) if elo_id_str else None
        except (ValueError, TypeError, OverflowError):
            print(f"[경고] {row_idx}행({soop_id})의 elo_id '{elo_id}'를 숫자로 해석할 수 없습니다.",
                  file=sys.stderr)
            elo_id_int = None

        rows[soop_id] = {
            "nickname": str(nickname).strip(),
            "elo_id": elo_id_int,
            "birthdate": sheet_format_date(birthdate),
            "gender": SHEET_GENDER_MAP.get(gender_raw, gender_raw) if gender_raw else None,
            "race": sheet_clean(race),
            "tier": str(tier) if tier is not None else None,
            "team": sheet_clean(team),
            "role": str(role).strip() if role else "",
            "info_updated_at": sheet_format_date(updated_at),
        }
    return rows

def write_sheet(update_rows: dict, append_rows: list, delete_ids: set | None = None,
                clear_info_updated_at: set | None = None) -> bool:
    """시트를 갱신한다. 실제로 썼으면 True, 건너뛰었으면 False.

    (예외는 절대 밖으로 던지지 않는다 - 호출부에서 이미 끝난 작업까지
    실패로 보이게 만들지 않기 위함. 대신 반환값으로 성공 여부를 알린다.)"""
    delete_ids = delete_ids or set()
    clear_info_updated_at = clear_info_updated_at or set()

    if not (update_rows or append_rows or delete_ids or clear_info_updated_at):
        return True

    try:
        ws = get_worksheet()
        if ws is None:
            print("[경고] 구글 시트 인증 정보가 없어 쓰기를 건너뜁니다.", file=sys.stderr)
            return False
        all_values = _sheet_call(ws.get_all_values, what=f"'{SHEET_NAME}' 시트 읽기(쓰기 전)")
    except Exception as e:
        # 여기서 실패해도 update_data.py의 나머지 작업(latest.json 저장 등)은
        # 이미 끝난 뒤라, 예외를 그대로 던지면 이미 완료된 작업의 성공 여부까지
        # 덩달아 실패로 보이게 된다. 시트 쓰기만 이번엔 건너뛰고 다음 실행에서
        # 다시 시도되게 한다(신규 미상 등록/수정일 비우기 정도라 하루 늦어져도
        # 치명적이지 않다).
        print(f"[오류] 구글 시트 쓰기 중 에러 발생 - 이번엔 건너뜁니다: {e}", file=sys.stderr)
        return False

    # 1행(헤더)은 아예 배열에 담지 않고 무시합니다. 2행부터 들어갈 데이터만 조립합니다.
    new_data = []

    if len(all_values) > 1:
        for row in all_values[1:]:
            # 무조건 A~J열(10개) 크기로 맞추어 K열 이후 데이터는 건드리지 않게 방어
            row = (row + [''] * 10)[:10] 
            cell_id = str(row[1]).strip() if row[1] else None

            if cell_id in delete_ids:
                continue

            fields = update_rows.get(cell_id)
            if fields:
                # 중요: "값이 없으면 빈 문자열로 덮어쓰기"를 하지 않는다.
                # 예전에는 fields의 어떤 값이 None이면 그 셀을 ""로 지워버렸다.
                # 이 write_sheet()의 유일한 호출부(sync_members)는 닉네임/종족/
                # 티어/팀만 바꾸려는 것이지 생년월일·성별을 건드릴 의도가
                # 전혀 없는데도, 시트에 "2000.01.01"처럼 ISO가 아닌 생년월일이
                # 있거나 성별 셀이 비어 있으면 그 사람의 데이터가 조용히
                # 삭제됐다(실제 데이터 손실 경로). 이제는 "호출부가 실제 값을
                # 준 칸만" 덮어쓰고, 나머지는 시트에 있던 값을 그대로 둔다.
                def _set(idx, value):
                    if value not in (None, ""):
                        row[idx] = value

                _set(0, fields.get("nickname"))
                _set(2, str(fields["elo_id"]) if fields.get("elo_id") is not None else None)
                _set(3, sheet_parse_date_for_write(fields.get("birthdate")))
                gender_value = fields.get("gender")
                _set(4, SHEET_GENDER_MAP_REVERSE.get(gender_value, gender_value))
                _set(5, fields.get("race"))
                _set(6, fields.get("tier"))
                _set(7, fields.get("team"))
                _set(8, fields.get("role"))

            if cell_id in clear_info_updated_at:
                row[9] = ""

            new_data.append(row)

    for m in append_rows:
        new_row = [
            m.get("nickname") or "",
            m.get("id") or "",
            str(m.get("elo_id")) if m.get("elo_id") is not None else "",
            sheet_parse_date_for_write(m.get("birthdate")),
            SHEET_GENDER_MAP_REVERSE.get(m.get("gender"), m.get("gender")) or "",
            m.get("race") or "",
            m.get("tier") or "",
            m.get("team") or "",
            m.get("role") or "",
            sheet_parse_date_for_write(m.get("info_updated_at"))
        ]
        new_data.append(new_row)

    # 아래 실제 쓰기 호출들도 반드시 try 안에 있어야 한다. 예전에는 읽기만
    # try로 감싸져 있어서, ws.update()/batch_clear()가 던지는 예외(쿼터 초과,
    # 일시적 500 등)가 그대로 위로 올라가 update_data.py를 죽였다. 이 시점은
    # latest.json 저장까지 이미 다 끝난 뒤라, 그렇게 죽으면 워크플로우가
    # 실패로 끝나면서 그날 수집한 데이터가 커밋조차 안 되고 통째로 버려진다.
    try:
        _write_sheet_values(ws, new_data, all_values)
        return True
    except Exception as e:
        print(f"[오류] 구글 시트 쓰기 중 에러 발생 - 이번엔 건너뜁니다: {e}", file=sys.stderr)
        return False

def _write_sheet_values(ws, new_data: list, all_values: list) -> None:
    # 1. 2행(A2)부터 시작하여 A~J열 영역의 "값"만 덮어씁니다. (1행 헤더와 K열 이후, 모든 서식 완벽 보존)
    if new_data:
        # value_input_option="RAW"를 쓴다 - "USER_ENTERED"였다면 구글시트가
        # 우리가 보낸 문자열을 "사람이 직접 입력한 것"처럼 해석해서, elo_id
        # 같은 숫자처럼 보이는 값은 진짜 숫자 셀로, "수정일" 같은 날짜처럼
        # 보이는 값은 진짜 날짜 셀로 자동 변환해버릴 수 있다. 그러면 나중에
        # 다시 읽어올 때 시트의 로케일/서식 설정에 따라 우리가 쓴 것과 다른
        # 문자열 형태(예: "2026-08-27"이 "2026. 8. 27"로)로 돌아올 위험이
        # 있고, 이 프로젝트는 날짜를 문자열 그대로 비교하는 곳이 많아서
        # (file_date >= upd["update_date"] 등) 이런 왕복 불일치가 치명적이다.
        # RAW는 이런 자동 타입 변환을 안 하고 보낸 문자열 그대로 저장한다.
        _sheet_call(ws.update, values=new_data, range_name="A2", value_input_option="RAW",
                    what=f"'{SHEET_NAME}' 시트 쓰기")

    # 2. 만약 삭제된 인원이 있어서 전체 행 수가 줄었다면 남은 찌꺼기 비우기
    old_data_count = len(all_values) - 1 if len(all_values) > 1 else 0
    new_data_count = len(new_data)

    if old_data_count > new_data_count:
        start_clear_row = 2 + new_data_count
        end_clear_row = len(all_values)
        # 찌꺼기가 남은 행의 A~J열 "값"만 명시적으로 삭제합니다. (서식 보존)
        _sheet_call(ws.batch_clear, [f"A{start_clear_row}:J{end_clear_row}"],
                    what=f"'{SHEET_NAME}' 시트 잔여행 정리")

# ---------------------------------------------------------------------------
# "신규 후보" (new_members) 시트 관련 헬퍼
# ---------------------------------------------------------------------------

def load_pending_members():
    """new_members 시트에 이미 대기 중인 후보 목록을 id(soop_id 또는
    elo_<elo_id> 형태의 placeholder) 기준으로 읽어온다. 호출부는 이 결과를
    이용해 "이미 대기 중인 후보"를 다시 추가하지 않게 걸러낸다 - 이게 없으면
    검토가 안 끝난 후보가 매 실행마다 new_members 시트에 중복으로 계속
    쌓이게 된다.
    load_sheet_members()와 마찬가지로, 읽기 실패는 None으로 구분해서
    반환한다(진짜로 대기 중인 후보가 0명인 것과는 다른 상황이므로)."""
    if not is_sheet_ready():
        return {}
    try:
        ws = get_worksheet(PENDING_SHEET_NAME)
        if ws is None:
            return {}
        all_values = _sheet_call(ws.get_all_values, what=f"'{PENDING_SHEET_NAME}' 시트 읽기")
    except Exception as e:
        print(f"[오류] '{PENDING_SHEET_NAME}' 시트를 읽어오는 중 에러 발생: {e}", file=sys.stderr)
        return None

    if not all_values or len(all_values) < 2:
        return {}

    rows = {}
    for row in all_values[1:]:
        row = (list(row) + [''] * PENDING_SHEET_COLUMNS)[:PENDING_SHEET_COLUMNS]
        nickname, cand_id, elo_id, gender, race, tier, team, source, found_at = row
        cand_id = str(cand_id).strip()
        if not cand_id:
            continue
        rows[cand_id] = {
            "nickname": str(nickname).strip() if nickname else "",
            "elo_id": normalize_elo_id(elo_id),
            "gender": str(gender).strip() if gender else "",
            "race": sheet_clean(race),
            "tier": sheet_clean(tier),
            "team": sheet_clean(team),
            "source": str(source).strip() if source else "",
            "found_at": str(found_at).strip() if found_at else "",
        }
    return rows

def normalize_elo_id(value) -> str:
    """elo_id를 "중복 판정에 쓸 수 있는" 하나의 표준 문자열로 만든다.

    같은 사람의 elo_id가 경로에 따라 6199(int) / "6199"(문자열) /
    "6199.0"(시트가 숫자 서식으로 저장한 경우)로 제각각 들어오는데, 이걸
    문자열 그대로 집합에 넣고 비교하면 "이미 대기 중인 후보"를 못 알아보고
    new_members 시트에 매 실행마다 같은 사람이 새 행으로 계속 쌓인다."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (ValueError, TypeError, OverflowError):
        return text

def append_pending_members(candidates: list) -> bool:
    """새로 발견된 후보들을 new_members 시트 맨 끝에 추가만 한다(수정/삭제
    없음 - 검토/승격/삭제는 관리자가 시트에서 직접 한다). 여기서 실패해도
    예외를 던지지 않는다 - 호출부(update_data.py, sync_members.py)에서
    latest.json 저장 등 이미 끝난 다른 작업까지 실패로 보이게 만들고 싶지
    않기 때문이다(write_sheet()의 실패 처리 방침과 동일).

    대신 실제로 썼는지를 bool로 알려준다 - 예전엔 반환값이 없어서 호출부가
    쓰기 실패 여부와 무관하게 "[완료] N명 추가되었습니다"를 찍었고, 로그만
    보고 있으면 추가된 줄 알았다가 시트엔 아무것도 없는 상황이 됐다."""
    if not candidates:
        return True
    try:
        ws = get_worksheet(PENDING_SHEET_NAME)
        if ws is None:
            print(f"[경고] '{PENDING_SHEET_NAME}' 시트 인증 정보가 없어 쓰기를 건너뜁니다.", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[오류] '{PENDING_SHEET_NAME}' 시트 접근 중 에러 발생 - 이번엔 건너뜁니다: {e}", file=sys.stderr)
        return False

    new_rows = []
    for c in candidates:
        new_rows.append([
            c.get("nickname") or "",
            c.get("id") or "",
            str(c.get("elo_id")) if c.get("elo_id") is not None else "",
            SHEET_GENDER_MAP_REVERSE.get(c.get("gender"), c.get("gender")) or "",
            c.get("race") or "",
            c.get("tier") or "",
            c.get("team") or "",
            c.get("source") or "",
            c.get("found_at") or "",
        ])

    try:
        # RAW를 쓰는 이유는 write_sheet()와 동일 - elo_id 같은 숫자처럼
        # 보이는 값이나 발견일 같은 날짜처럼 보이는 값이 구글 시트에 의해
        # 자동으로 다른 타입으로 변환되는 것을 막기 위함이다.
        _sheet_call(ws.append_rows, new_rows, value_input_option="RAW",
                    what=f"'{PENDING_SHEET_NAME}' 시트 후보 추가")
        return True
    except Exception as e:
        print(f"[오류] '{PENDING_SHEET_NAME}' 시트에 신규 후보 추가 중 에러 발생 - 이번엔 건너뜁니다: {e}", file=sys.stderr)
        return False
