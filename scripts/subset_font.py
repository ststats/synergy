"""
Pretendard Variable 전체 파일(약 2.3MB - 모든 굵기 + 완성형 한글 11,172자
전부 포함)을 다운로드해서, 이 사이트가 실제로 표시하는 문자만 추려낸 훨씬
작은 서브셋(docs/fonts/PretendardVariable.woff2)으로 만든다.

CDN의 "다이나믹 서브셋" 방식(unicode-range로 브라우저가 알아서 필요한 조각만
받게 하는 것)도 검토했는데, 그건 font-display 값을 CDN이 이미 고정해둬서
우리가 못 바꾼다. 여기선 자체호스팅이라 font-display도 우리가 원하는 값
(optional - 깜빡임 없이, 느리면 그냥 기본 글꼴로)을 그대로 쓸 수 있다.

실제로 쓰는 문자를 매번 다시 스캔해서 서브셋을 새로 만들기 때문에, 로스터가
바뀌어서(신규 멤버 추가 등) 새로운 한글 음절이 필요해져도 다음 실행에서
자동으로 반영된다 - 파일을 고정해두고 잊어버리는 방식이 아니다.

문자 수집 범위:
  1. 현재 로스터(members.json)의 닉네임/소속/종족/티어/직책
  2. generate_pages.py가 만든 docs/*.html - 고정 UI 문구(제목, 라벨, 안내
     텍스트 등)
  3. 기본 라틴/숫자/기호 + 자주 쓰는 한글 자모(안전판 - 위 스캔에서 혹시
     놓친 게 있어도 최소한 이 정도는 항상 포함)

주의: 이 스크립트는 generate_pages.py가 끝난 뒤에 실행돼야 한다(2번 범위가
방금 생성된 HTML을 스캔하기 때문).
Actions 러너는 매번 새로 시작해 지난 실행 기록이 없으므로 매번 새로 만든다(몇 초 걸림).
"""

import sys
import json
import time
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

FONT_DOWNLOAD_RETRIES = 3
FONT_DOWNLOAD_BACKOFF_SEC = 3
# woff2 파일의 시그니처. CDN이 에러 페이지(HTML)를 200으로 돌려주는 경우를
# 걸러내기 위함 - 그걸 그대로 fonttools에 넘기면 훨씬 뒤에서 알아보기 힘든
# 에러로 터진다.
WOFF2_MAGIC = b"wOF2"

from _common import ROOT

FONT_URL = (
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
    "packages/pretendard/dist/web/variable/woff2/PretendardVariable.woff2"
)
OUTPUT_PATH = ROOT / "docs" / "fonts" / "PretendardVariable.woff2"
MEMBERS_PATH = ROOT / "data" / "members.json"
DOCS_DIR = ROOT / "docs"

MEMBER_TEXT_FIELDS = ("nickname", "team", "race", "tier", "role")

# 위 스캔에서 혹시 놓친 문자가 있어도 최소한 이 정도는 항상 포함해두는 안전판.
BASE_CHARS = (
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
    "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㄲㄸㅃㅆㅉㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅐㅔㅘㅙㅚㅝㅞㅟㅢ"
)


def _add_member_text(chars: set, members: list) -> None:
    for m in members:
        for field in MEMBER_TEXT_FIELDS:
            v = m.get(field)
            if v:
                chars.update(str(v))


def collect_used_characters() -> set:
    chars = set(BASE_CHARS)

    if MEMBERS_PATH.exists():
        try:
            with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _add_member_text(chars, data.get("members", []))
        except Exception as e:
            print(f"[경고] members.json 읽기 실패, 건너뜀: {e}", file=sys.stderr)

    if DOCS_DIR.exists():
        for p in DOCS_DIR.glob("*.html"):
            try:
                chars.update(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[경고] {p.name} 읽기 실패, 건너뜀: {e}", file=sys.stderr)

    # 출력 불가능한 제어문자 등은 폰트에 넣을 필요 없음(공백은 이미 BASE_CHARS에 있어 예외 처리)
    chars = {c for c in chars if c == " " or c.isprintable()}
    return chars


def _download_font():
    """폰트 원본을 받아서 bytes로 돌려준다. 실패하면 None.

    예전엔 단발 요청이라 CDN이 한 번만 흔들려도 그날 폰트 갱신이 통째로
    건너뛰어졌다(워크플로우가 continue-on-error라 조용히 넘어간다).
    또 응답이 실제 woff2인지 확인하지 않아서, CDN 에러 페이지를 폰트로
    착각해 fonttools 단계에서야 이상한 에러로 터졌다."""
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
    chars = collect_used_characters()
    print(f"[준비] 실제 사용 문자 {len(chars)}개 확인")


    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_full = Path(tmpdir) / "PretendardVariable-full.woff2"

        print("[다운로드] Pretendard Variable 전체 파일 받는 중...")
        content = _download_font()
        if content is None:
            sys.exit(1)
        tmp_full.write_bytes(content)
        print(f"[완료] {len(content) / 1024 / 1024:.2f}MB 다운로드됨")

        # 문자가 수천 개가 되면 --unicodes= 인자 하나가 수만 바이트가 되어
        # 명령줄 길이 제한(ARG_MAX)에 가까워진다. fonttools가 제공하는
        # 파일 입력 방식을 쓰면 이 위험 자체가 사라진다.
        unicodes_file = Path(tmpdir) / "unicodes.txt"
        unicodes_file.write_text(
            "\n".join(f"U+{ord(c):04X}" for c in sorted(chars)), encoding="utf-8"
        )

        # 서브셋 결과는 임시 파일에 먼저 만들고, 완전히 성공했을 때만
        # 최종 위치로 옮긴다. 예전엔 fonttools가 OUTPUT_PATH에 직접 쓰게
        # 해서, 중간에 실패하면 기존의 멀쩡한 폰트가 반쯤 쓰인 파일로
        # 덮여 사이트 글꼴이 깨질 수 있었다.
        tmp_out = Path(tmpdir) / "subset.woff2"
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "fontTools.subset",
            str(tmp_full),
            f"--unicodes-file={unicodes_file}",
            f"--output-file={tmp_out}",
            "--flavor=woff2",
            # fonttools의 기본 --layout-features 값은 tnum(탭 숫자 - 모든 숫자가
            # 똑같은 폭을 갖게 하는 OpenType 기능)을 자동으로는 안 챙긴다 - 이걸
            # 빼먹으면 사이트 CSS의 font-variant-numeric: tabular-nums가 있어도
            # 실제 폰트 파일에 그 기능이 없어서 무용지물이 되고, 숫자마다 폭이
            # 달라져서 별풍선 수치 같은 게 줄이 안 맞고 들쭉날쭉해 보인다.
            # lnum(ライニング 숫자, 소문자 x-height 안에 갇히지 않는 일반적인
            # 숫자 형태)도 같이 챙겨서 숫자 스타일이 서브셋 전후로 안 바뀌게 한다.
            "--layout-features+=tnum,lnum",
        ]
        print(f"[서브셋] fonttools로 {len(chars)}개 문자만 추려내는 중...")
        # timeout이 없으면 fonttools가 어떤 이유로 멈췄을 때 워크플로우
        # 타임아웃(25분)까지 그대로 매달려 있게 된다.
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
