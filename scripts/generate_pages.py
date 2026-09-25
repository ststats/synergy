"""
synergy 프로젝트의 CSR 페이지 생성 스크립트.
"""

import sys
import json
import io
import shutil
from pathlib import Path
from PIL import Image

from _common import ROOT, safe_read_json
from jinja2 import Environment, FileSystemLoader

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
# 사이트 아이콘 원본(파비콘·숲 로고). 대학 로고는 스타유니브 어드민(전적 > 팀 관리)에서 올리고
# 페이지가 공유 Supabase의 university_logos 표에서 주소와 카드 색을 읽는다.
LOGOS_DIR = ROOT / "assets" / "logos"
# 사이트에 싣는 작은 사본(긴 변 96px). 빌드가 만든다.
WEB_LOGOS_DIR = DOCS_DIR / "logos"
WEB_LOGO_SIZE = 96
OUTPUT_INDEX = DOCS_DIR / "index.html"
OUTPUT_PROFILE_PATH = DOCS_DIR / "profile.html"
OUTPUT_TEAM_PATH = DOCS_DIR / "team.html"
OUTPUT_TEAMS_DIR = DOCS_DIR / "teams" 

DEFAULT_TOPBAR_COLOR = "#4a5ce0"


def json_for_script(value) -> str:
    """<script> 태그 안에 JS 리터럴로 박아 넣어도 안전한 JSON 문자열을 만든다.

    이 프로젝트의 Jinja Environment는 autoescape가 꺼져 있고(HTML이 아니라
    JS/CSS를 주로 렌더하므로 켜는 것도 답이 아니다), 그래서 JSON을 넣는
    자리에는 json.dumps 결과가 그대로 들어간다. 문제는 json.dumps가
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


def build_web_logos() -> None:
    """assets/logos 원본을 작은 webp로 줄여 docs/logos에 둔다. 원본이 없는 사본은 지운다."""
    WEB_LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    sources = {p.name: p for p in LOGOS_DIR.glob("*.webp")}
    for name, src in sources.items():
        # 매번 새로 줄이되(작은 파일 20개 남짓) 내용이 같으면 쓰지 않는다 - 빌드 커밋에 안 섞이게
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail((WEB_LOGO_SIZE, WEB_LOGO_SIZE), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=90, method=6)
        dst = WEB_LOGOS_DIR / name
        if not dst.exists() or dst.read_bytes() != buf.getvalue():
            dst.write_bytes(buf.getvalue())
    for old in WEB_LOGOS_DIR.glob("*.webp"):
        if old.name not in sources:
            old.unlink()


def generate_html(title, target_team, is_profile, logo_prefix, team_colors, team_from_url=False):
    font_url = f"{logo_prefix}fonts/PretendardVariable.woff2"
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

def main():
    members_config = safe_read_json(MEMBERS_PATH, default=None)
    if not isinstance(members_config, dict):
        # 예전엔 open()을 바로 해서, members.json이 없거나 깨져 있으면
        # FileNotFoundError/JSONDecodeError로 죽으면서 원인이 안 남았다.
        # (convert_members.py 스텝은 continue-on-error라 실제로 발생 가능한 경로다.)
        print(f"[오류] {MEMBERS_PATH}를 읽을 수 없거나 형식이 올바르지 않습니다.", file=sys.stderr)
        sys.exit(1)
    members_data = [m for m in members_config.get("members", []) if isinstance(m, dict)]

    all_team_names = {
        m.get("team") for m in members_data
        if m.get("team") and m.get("team") not in ("FA", "휴면", "미분류")
    }

    # 대학 카드 색은 페이지가 university_logos 표에서 받아 덮어쓴다. 여기 값은 표를 못 읽었을 때의 기본색
    team_colors = {team: DEFAULT_TOPBAR_COLOR for team in sorted(all_team_names)}
    team_colors["FA"], team_colors["휴면"] = "#8b8f99", "#8b8f99"
    build_web_logos()

    index_html = generate_html("시너지", "", False, "", team_colors)
    OUTPUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    _write_if_changed(OUTPUT_INDEX, index_html)

    if OUTPUT_TEAMS_DIR.exists():
        shutil.rmtree(OUTPUT_TEAMS_DIR)

    if all_team_names:
        any_team = sorted(all_team_names)[0]
        team_html = generate_html("팀별 현황", any_team, False, "", team_colors,
                                   team_from_url=True)
        _write_if_changed(OUTPUT_TEAM_PATH, team_html)

    profile_html = generate_html("프로필", "", True, "", team_colors)
    _write_if_changed(OUTPUT_PROFILE_PATH, profile_html)

if __name__ == "__main__":
    main()
