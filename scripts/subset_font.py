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
  2. 과거 아카이브(archive/*.json) 전체 - 지금은 로스터에 없지만 과거 날짜를
     browsing할 때 여전히 표시될 수 있는 사람들의 닉네임/팀 이름을 놓치지
     않기 위함
  3. generate_pages.py가 만든 docs/*.html - 고정 UI 문구(제목, 라벨, 안내
     텍스트 등)
  4. 기본 라틴/숫자/기호 + 자주 쓰는 한글 자모(안전판 - 위 스캔에서 혹시
     놓친 게 있어도 최소한 이 정도는 항상 포함)

주의: 이 스크립트는 generate_pages.py가 끝난 뒤에 실행돼야 한다(3번 범위가
방금 생성된 HTML을 스캔하기 때문).
"""

import sys
import json
import time
import shutil
import hashlib
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

from _common import ROOT, safe_read_json, atomic_write_json

FONT_URL = (
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
    "packages/pretendard/dist/web/variable/woff2/PretendardVariable.woff2"
)
OUTPUT_PATH = ROOT / "docs" / "fonts" / "PretendardVariable.woff2"
MEMBERS_PATH = ROOT / "data" / "members.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
DOCS_DIR = ROOT / "docs"
# 매번 문자를 다시 스캔하는 것 자체는 가벼운데(멤버 수가 많아야 수백~수천
# 정도), 그 결과로 2.3MB 원본을 새로 받고 fonttools 서브셋을 돌리는 건
# 무겁다 - 문자 구성이 바뀐 게 없는 날(대부분의 날)까지 매번 이 무거운
# 작업을 반복할 이유가 없다. 그래서 이 프로젝트의 다른 곳들(members_sync_baseline.json,
# archive_corrections_applied.json)처럼 스냅샷을 남겨서, 이전 실행이랑
# 문자 구성이 완전히 같으면 다운로드+서브셋 자체를 건너뛴다.
CHARS_SNAPSHOT_PATH = ROOT / "data" / "font_subset_chars_hash.json"

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

    if ARCHIVE_DIR.exists():
        for p in ARCHIVE_DIR.rglob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _add_member_text(chars, data.get("members", []))
            except Exception:
                continue  # 아카이브 파일 하나 깨져있다고 전체를 멈출 이유는 없음

    if DOCS_DIR.exists():
        for p in DOCS_DIR.glob("*.html"):
            try:
                chars.update(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[경고] {p.name} 읽기 실패, 건너뜀: {e}", file=sys.stderr)

    # 출력 불가능한 제어문자 등은 폰트에 넣을 필요 없음(공백은 이미 BASE_CHARS에 있어 예외 처리)
    chars = {c for c in chars if c == " " or c.isprintable()}
    return chars


# 캐시 판단에 문자 구성뿐 아니라 이 버전 번호도 같이 넣는다 - 문자 구성이
# 하나도 안 바뀌어도, fonttools 서브셋 명령어 자체(예: --layout-features
# 옵션)가 바뀌면 예전 캐시가 여전히 "완전히 동일함"으로 잘못 판단해서
# 재생성을 건너뛰어 버린다. 서브셋 로직을 고칠 때마다 이 숫자를 올려서,
# 다음 실행에서 확실히 한 번은 다시 만들어지게 한다.
SUBSET_LOGIC_VERSION = 2  # v2: --layout-features+=tnum,lnum 추가(별풍선 등 숫자 폭이
                          # 안 맞던 문제 수정 - v1은 이 옵션이 없어서 tabular-nums가
                          # 실제로는 작동 안 했었음)


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

    # 문자 구성 + 서브셋 로직 버전이 지난 실행이랑 완전히 같으면(대부분의 날이
    # 그렇다 - 로스터나 페이지 UI 문구, 서브셋 로직 자체가 매일 바뀌는 게
    # 아니므로), 서브셋 결과도 어차피 똑같이 나올 거라 무거운 다운로드+서브셋
    # 작업 자체를 건너뛴다. 기존 docs/fonts/PretendardVariable.woff2는 지난
    # 실행 결과 그대로 남는다.
    chars_hash = hashlib.sha256(
        f"{SUBSET_LOGIC_VERSION}:{''.join(sorted(chars))}".encode("utf-8")
    ).hexdigest()
    snapshot = safe_read_json(CHARS_SNAPSHOT_PATH, default={})
    if snapshot.get("hash") == chars_hash and OUTPUT_PATH.exists():
        print("[건너뜀] 지난 실행과 사용 문자 구성이 완전히 동일함 - 폰트 재생성 생략")
        return

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

    # 서브셋 성공한 뒤에만 스냅샷을 갱신한다 - 중간에 실패했으면 다음 실행에서
    # 다시 시도해야 하므로, 실패한 실행의 해시를 "완료됨"으로 잘못 남기면 안 된다.
    atomic_write_json(CHARS_SNAPSHOT_PATH, {"hash": chars_hash, "char_count": len(chars)})


if __name__ == "__main__":
    main()
