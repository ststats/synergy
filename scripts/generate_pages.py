"""
synergy 프로젝트의 CSR 페이지 생성 스크립트.
"""

import sys
import json
import re
import colorsys
import hashlib
import shutil
from pathlib import Path
from PIL import Image

from _common import ROOT, safe_read_json, atomic_write_json
from jinja2 import Environment, FileSystemLoader

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DATA_PATH = ROOT / "data" / "latest.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
MEMBERS_PATH = ROOT / "data" / "members.json"
DOCS_DIR = ROOT / "docs"
SCRIPTS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPTS_DIR / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
DOCS_DATA_DIR = DOCS_DIR / "data" / "daily"
LOGOS_DIR = DOCS_DIR / "logos"
OUTPUT_INDEX = DOCS_DIR / "index.html"
OUTPUT_PROFILE_PATH = DOCS_DIR / "profile.html"
OUTPUT_TEAM_PATH = DOCS_DIR / "team.html"
OUTPUT_TEAMS_DIR = DOCS_DIR / "teams" 
TEAM_LOGO_COLOR_CACHE_PATH = ROOT / "data" / "team_logo_colors_cache.json"

PLACEHOLDER_VALUES = {"체크", "todo", "TODO", "?", "미정", "확인", "확인필요", ""}
DEFAULT_TOPBAR_COLOR = "#4a5ce0"


def json_for_script(value) -> str:
    """<script> 태그 안에 JS 리터럴로 박아 넣어도 안전한 JSON 문자열을 만든다.

    이 프로젝트의 Jinja Environment는 autoescape가 꺼져 있고(HTML이 아니라
    JS/CSS를 주로 렌더하므로 켜는 것도 답이 아니다), 그래서 `{{ static_json }}`
    같은 자리에는 json.dumps 결과가 그대로 들어간다. 문제는 json.dumps가
    "<", ">"를 전혀 escape하지 않는다는 점이다 - 팀 이름이나 닉네임(구글
    시트를 거치지만 원천은 EloBoard API의 college/name 필드다)에
    "</script><script>...</script>" 같은 문자열이 한 번이라도 들어오면
    스크립트 블록이 그 자리에서 끊기고 임의 JS가 방문자 전원의 브라우저에서
    실행된다(저장형 XSS).

    - `<`, `>`, `&`: 태그/엔티티로 해석될 여지를 없앤다
    - U+2028/U+2029: JSON에서는 합법이지만 JS 소스에서는 줄바꿈으로 취급돼
      문법 오류를 내는 고전적인 함정
    이렇게 escape해도 JSON 값 자체는 의미가 완전히 동일하다."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def get_team_topbar_color(team_name: str, cache: dict) -> str:
    # 팀 이름은 시트에서 온 임의의 문자열이다. "../"나 "/"가 섞이면
    # LOGOS_DIR 밖의 파일을 열게 된다(읽기 전용이라 피해는 제한적이지만,
    # 경로 조립에 검증 없는 외부 문자열을 쓰는 패턴은 남겨둘 이유가 없다).
    if not team_name or "/" in team_name or "\\" in team_name or team_name in (".", ".."):
        return DEFAULT_TOPBAR_COLOR
    logo_path = LOGOS_DIR / f"{team_name}.webp"
    try:
        if logo_path.resolve().parent != LOGOS_DIR.resolve():
            return DEFAULT_TOPBAR_COLOR
    except OSError:
        return DEFAULT_TOPBAR_COLOR
    if not logo_path.exists():
        return DEFAULT_TOPBAR_COLOR

    try:
        file_hash = hashlib.sha256(logo_path.read_bytes()).hexdigest()
    except OSError:
        return DEFAULT_TOPBAR_COLOR

    cached = cache.get(team_name)
    if cached and cached.get("hash") == file_hash:
        return cached["color"]

    color = DEFAULT_TOPBAR_COLOR
    try:
        # with로 열어 파일 핸들을 확실히 닫는다 - 예전엔 Image.open()의
        # 핸들이 GC에 맡겨져서 팀이 많을수록 ResourceWarning이 쌓였다.
        with Image.open(logo_path) as src:
            img = src.convert("RGBA").resize((40, 40))
        buckets = {}
        for r, g, b, a in img.getdata():
            if a < 128 or (r > 235 and g > 235 and b > 235) or (r < 20 and g < 20 and b < 20): continue
            _, saturation, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            key = (r // 20 * 20, g // 20 * 20, b // 20 * 20)
            bucket = buckets.setdefault(key, [0, 0.0])
            bucket[0] += 1
            bucket[1] += saturation
        if buckets:
            top_buckets = sorted(buckets.items(), key=lambda kv: -kv[1][0])[:6]
            best_key = max(top_buckets, key=lambda kv: kv[1][1] / kv[1][0])[0]
            color = f"#{best_key[0]:02x}{best_key[1]:02x}{best_key[2]:02x}"
    except Exception as e:
        # 조용히 넘기면 "왜 이 팀만 기본 색이지?"를 영영 알 수 없다.
        print(f"[경고] '{team_name}' 로고에서 색을 뽑지 못해 기본색을 씁니다: {e}", file=sys.stderr)

    cache[team_name] = {"hash": file_hash, "color": color}
    return color

def clean_value(v): return None if str(v).strip() in PLACEHOLDER_VALUES else v

def generate_html(title, target_team, is_profile, logo_prefix, static_info, team_colors, team_from_url=False):
    font_url = f"{logo_prefix}fonts/PretendardVariable.woff2"
    static_json = json_for_script(static_info)
    colors_json = json_for_script(team_colors)

    if team_from_url:
        target_team_js = "new URLSearchParams(window.location.search).get('team') || \"\""
    else:
        # 예전엔 f'"{team_name_str}"'로 팀 이름을 JS 문자열 리터럴 안에 그대로
        # 끼워 넣었다. 팀 이름에 따옴표나 역슬래시, 줄바꿈이 하나만 있어도
        # 스크립트가 문법 오류로 통째로 죽고(= 페이지 전체 백지), 악의적인
        # 값이면 임의 코드 실행이 된다. json.dumps는 이 모든 경우를 정확히
        # 처리하는 표준 방식이다.
        target_team_js = json_for_script(target_team if target_team else "")

    include_mobile_css = not is_profile and not target_team
    template = _jinja_env.get_template("page.html.j2")
    return template.render(
        colors_json=colors_json,
        font_url=font_url,
        include_mobile_css=include_mobile_css,
        is_profile=is_profile,
        logo_prefix=logo_prefix,
        static_info=static_info,
        static_json=static_json,
        target_team=target_team,
        target_team_js=target_team_js,
        team_colors=team_colors,
        team_from_url=team_from_url,
        title=title,
    )

def _write_if_changed(dst_path: Path, content: str) -> None:
    if dst_path.exists():
        try:
            if dst_path.read_text(encoding="utf-8") == content:
                return
        except Exception:
            pass
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(content)

def _copy_if_changed(src_path: Path, dst_path: Path) -> None:
    if dst_path.exists() and dst_path.stat().st_size == src_path.stat().st_size:
        try:
            if dst_path.read_bytes() == src_path.read_bytes():
                return
        except Exception:
            pass
    shutil.copy(src_path, dst_path)

def main():
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_dates = []

    latest = safe_read_json(DATA_PATH, default=None)
    if isinstance(latest, dict):
        d_str = latest.get("date")
        # 이 값이 그대로 파일명이 되므로 형식을 반드시 검증한다.
        if isinstance(d_str, str) and DATE_RE.match(d_str):
            all_dates.append(d_str)
            _write_if_changed(DOCS_DATA_DIR / f"{d_str}.json", json.dumps(latest, ensure_ascii=False))
        elif d_str:
            print(f"[경고] latest.json의 date 형식이 올바르지 않아 무시합니다: {d_str!r}", file=sys.stderr)

    if ARCHIVE_DIR.exists():
        # rglob를 사용하여 연/월 구조 내의 파일도 모두 탐색
        for arch in ARCHIVE_DIR.rglob("*.json"):
            if DATE_RE.match(arch.stem):
                all_dates.append(arch.stem)
                _copy_if_changed(arch, DOCS_DATA_DIR / arch.name)

    all_dates = sorted(set(all_dates), reverse=True)
    if not all_dates:
        # 조용히 return하면 "성공했는데 아무 일도 안 일어난" 것처럼 보인다.
        print("[오류] 사용 가능한 날짜 데이터가 하나도 없어 페이지를 생성하지 않았습니다 "
              "(data/latest.json과 data/archive를 확인하세요).", file=sys.stderr)
        sys.exit(1)

    expected_files = {f"{d_str}.json" for d_str in all_dates}
    for existing in DOCS_DATA_DIR.glob("*.json"):
        if existing.name not in expected_files:
            existing.unlink()

    dates_js_content = "window.AVAILABLE_DATES = " + json.dumps(all_dates) + ";\n"
    _write_if_changed(DOCS_DIR / "data" / "dates.js", dates_js_content)

    members_config = safe_read_json(MEMBERS_PATH, default=None)
    if not isinstance(members_config, dict):
        # 예전엔 open()을 바로 해서, members.json이 없거나 깨져 있으면
        # FileNotFoundError/JSONDecodeError로 죽으면서 원인이 안 남았다.
        # (convert_members.py 스텝은 continue-on-error라 실제로 발생 가능한 경로다.)
        print(f"[오류] {MEMBERS_PATH}를 읽을 수 없거나 형식이 올바르지 않습니다.", file=sys.stderr)
        sys.exit(1)
    members_data = [m for m in members_config.get("members", []) if isinstance(m, dict)]

    roster_ids = sorted({m.get("id") for m in members_data if m.get("id")})
    _write_if_changed(DOCS_DIR / "data" / "roster_ids.json", json.dumps(roster_ids, ensure_ascii=False))

    all_team_names = {
        m.get("team") for m in members_data
        if m.get("team") and m.get("team") not in ("FA", "휴면", "미분류")
    }

    static_info = {}
    for m in members_data:
        mid = m.get("id")
        if mid:
            static_info[mid] = {"gender": m.get("gender", "m"), "birthdate": clean_value(m.get("birthdate"))}

    team_color_cache = safe_read_json(TEAM_LOGO_COLOR_CACHE_PATH, default={})
    if not isinstance(team_color_cache, dict):
        team_color_cache = {}
    team_colors = {team: get_team_topbar_color(team, team_color_cache) for team in sorted(all_team_names)}
    # 해체된 팀의 캐시 항목을 계속 들고 있으면 이 파일이 영원히 커지기만 한다
    # (매 커밋에 실려 리포지토리에도 계속 쌓인다). 현재 팀만 남긴다.
    team_color_cache = {k: v for k, v in team_color_cache.items() if k in all_team_names}
    atomic_write_json(TEAM_LOGO_COLOR_CACHE_PATH, team_color_cache)
    team_colors["FA"], team_colors["휴면"] = "#8b8f99", "#8b8f99"

    index_html = generate_html("시너지", "", False, "", static_info, team_colors)
    OUTPUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    _write_if_changed(OUTPUT_INDEX, index_html)

    if OUTPUT_TEAMS_DIR.exists():
        shutil.rmtree(OUTPUT_TEAMS_DIR)

    if all_team_names:
        any_team = sorted(all_team_names)[0]
        team_html = generate_html("팀별 현황", any_team, False, "", static_info, team_colors,
                                   team_from_url=True)
        _write_if_changed(OUTPUT_TEAM_PATH, team_html)

    profile_html = generate_html("프로필", "", True, "", static_info, team_colors)
    _write_if_changed(OUTPUT_PROFILE_PATH, profile_html)

if __name__ == "__main__":
    main()
