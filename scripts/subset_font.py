"""Pretendard Variable을 사이트에 나오는 글자만 남긴 서브셋(docs/fonts)으로 만든다.

자체 호스팅은 font-display를 직접 정하기 위해서다(CDN 다이나믹 서브셋은 고정돼 있다).
docs/*.html을 읽으므로 generate_pages.py 다음에 돌린다.
"""

import os
import sys
import time
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from fontTools.ttLib import TTFont

FONT_DOWNLOAD_RETRIES = 3
FONT_DOWNLOAD_BACKOFF_SEC = 3
# CDN이 에러 페이지를 200으로 돌려주는 경우를 거른다.
WOFF2_MAGIC = b"wOF2"

ROOT = Path(__file__).resolve().parent.parent
FONT_URL = (
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
    "packages/pretendard/dist/web/variable/woff2/PretendardVariable.woff2"
)
OUTPUT_PATH = ROOT / "docs" / "fonts" / "PretendardVariable.woff2"
DOCS_DIR = ROOT / "docs"

MEMBER_TEXT_FIELDS = ("nickname", "affiliation", "tier", "role")

# 스캔에서 놓쳐도 항상 넣는 안전판.
BASE_CHARS = (
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
    "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㄲㄸㅃㅆㅉㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅐㅔㅘㅙㅚㅝㅞㅟㅢ"
    "테란저그프로토스랜덤"  # 프로필의 종족 칸(DB가 이 네 이름으로 맞춘다)
)


def _fetch_member_rows() -> list:
    """화면에 나오는 선수 목록. 조회 실패는 예외로 올려 생성을 멈춘다."""
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""
    if not url or not key:
        raise RuntimeError("SUPABASE_URL·SUPABASE_PUBLISHABLE_KEY가 없습니다")
    resp = requests.get(f"{url}/rest/v1/rpc/api_daily_stats", headers={"apikey": key}, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("선수 목록 응답 형식이 올바르지 않습니다")
    return rows


def collect_used_characters() -> set:
    chars = set(BASE_CHARS)
    # 지난 날짜에는 은퇴 멤버도 나오므로 기존 글리프는 명단에서 빠져도 남긴다.
    if OUTPUT_PATH.exists():
        with TTFont(OUTPUT_PATH) as previous:
            chars.update(chr(code) for code in previous.getBestCmap())

    for row in _fetch_member_rows():
        for field in MEMBER_TEXT_FIELDS:
            if row.get(field):
                chars.update(str(row[field]))

    # app.js가 그려 넣는 문구도 화면 글자다
    if DOCS_DIR.exists():
        # *.min.js는 외부 라이브러리라 화면 글자가 아니다
        js = [p for p in DOCS_DIR.glob("*.js") if not p.name.endswith(".min.js")]
        for p in [*DOCS_DIR.glob("*.html"), *js]:
            chars.update(p.read_text(encoding="utf-8"))

    chars = {c for c in chars if c == " " or c.isprintable()}
    return chars


def _download_font():
    """폰트 원본 bytes, 실패하면 None. 워크플로 단계가 continue-on-error라 조용히 넘어가므로 재시도한다."""
    for attempt in range(1, FONT_DOWNLOAD_RETRIES + 1):
        try:
            resp = requests.get(FONT_URL, timeout=60)
            resp.raise_for_status()
            content = resp.content
            if not content.startswith(WOFF2_MAGIC):
                raise ValueError(f"woff2 형식이 아닙니다(앞 4바이트={content[:4]!r})")
            return content
        except (requests.RequestException, ValueError) as e:
            print(f"[경고] 폰트 원본 다운로드 실패 ({attempt}/{FONT_DOWNLOAD_RETRIES}): {e}",
                  file=sys.stderr)
            if attempt < FONT_DOWNLOAD_RETRIES:
                time.sleep(FONT_DOWNLOAD_BACKOFF_SEC * attempt)
    print("[오류] 폰트 원본을 받지 못했습니다 - 기존 폰트 파일을 그대로 둡니다.", file=sys.stderr)
    return None


def main():
    try:
        chars = collect_used_characters()
    except Exception as e:
        print(f"[오류] 사용 문자 수집 실패 - 기존 폰트를 보존합니다: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[준비] 실제 사용 문자 {len(chars)}개 확인")


    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_full = Path(tmpdir) / "PretendardVariable-full.woff2"

        print("[다운로드] Pretendard Variable 전체 파일 받는 중...")
        content = _download_font()
        if content is None:
            sys.exit(1)
        tmp_full.write_bytes(content)
        print(f"[완료] {len(content) / 1024 / 1024:.2f}MB 다운로드됨")

        # 문자가 수천 개면 --unicodes= 인자가 ARG_MAX에 가까워져 파일로 넘긴다.
        unicodes_file = Path(tmpdir) / "unicodes.txt"
        unicodes_file.write_text(
            "\n".join(f"U+{ord(c):04X}" for c in sorted(chars)), encoding="utf-8"
        )

        # 실패해도 기존 폰트가 덮이지 않게 임시 파일에 만든 뒤 옮긴다.
        tmp_out = Path(tmpdir) / "subset.woff2"
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "fontTools.subset",
            str(tmp_full),
            f"--unicodes-file={unicodes_file}",
            f"--output-file={tmp_out}",
            "--flavor=woff2",
            # 기본 기능 목록에 tnum·lnum이 없어 CSS tabular-nums가 듣지 않는다.
            "--layout-features+=tnum,lnum",
        ]
        print(f"[서브셋] fonttools로 {len(chars)}개 문자만 추려내는 중...")
        # 멈추면 워크플로 시간 제한(20분)까지 매달리지 않게 한다.
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            print("[오류] 서브셋 작업이 300초를 넘겨 중단했습니다.", file=sys.stderr)
            sys.exit(1)
        if result.returncode != 0:
            print(f"[오류] 서브셋 실패:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

        if not tmp_out.exists() or tmp_out.stat().st_size == 0:
            print("[오류] 서브셋 결과 파일이 생성되지 않았습니다.", file=sys.stderr)
            sys.exit(1)

        shutil.move(str(tmp_out), str(OUTPUT_PATH))

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"[완료] {OUTPUT_PATH} 생성됨 ({size_kb:.1f}KB, 원본 대비 대폭 축소)")


if __name__ == "__main__":
    main()
