"""
synergy 프로젝트의 CSR 페이지 생성 스크립트.
"""

import json
import colorsys
import hashlib
import shutil
from pathlib import Path
from PIL import Image

from _common import ROOT, safe_read_json, atomic_write_json
from jinja2 import Environment, FileSystemLoader

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

def get_team_topbar_color(team_name: str, cache: dict) -> str:
    logo_path = LOGOS_DIR / f"{team_name}.webp"
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
        img = Image.open(logo_path).convert("RGBA").resize((40, 40))
        buckets = {}
        for r, g, b, a in img.getdata():
            if a < 128 or (r > 235 and g > 235 and b > 235) or (r < 20 and g < 20 and b < 20): continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            key = (r // 20 * 20, g // 20 * 20, b // 20 * 20)
            bucket = buckets.setdefault(key, [0, 0.0])
            bucket[0] += 1
            bucket[1] += s
        if buckets:
            top_buckets = sorted(buckets.items(), key=lambda kv: -kv[1][0])[:6]
            best_key = max(top_buckets, key=lambda kv: kv[1][1] / kv[1][0])[0]
            color = f"#{best_key[0]:02x}{best_key[1]:02x}{best_key[2]:02x}"
    except Exception:
        pass

    cache[team_name] = {"hash": file_hash, "color": color}
    return color

def clean_value(v): return None if str(v).strip() in PLACEHOLDER_VALUES else v

def generate_html(title, target_team, is_profile, logo_prefix, static_info, team_colors, team_from_url=False):
    font_url = f"{logo_prefix}fonts/PretendardVariable.woff2"
    static_json = json.dumps(static_info, ensure_ascii=False)
    colors_json = json.dumps(team_colors, ensure_ascii=False)
    
    if team_from_url:
        target_team_js = "new URLSearchParams(window.location.search).get('team') || \"\""
    else:
        team_name_str = target_team if target_team else ""
        target_team_js = f'"{team_name_str}"'

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

    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            latest = json.load(f)
            d_str = latest.get("date")
            if d_str:
                all_dates.append(d_str)
                _write_if_changed(DOCS_DATA_DIR / f"{d_str}.json", json.dumps(latest, ensure_ascii=False))

    if ARCHIVE_DIR.exists():
        # rglob를 사용하여 연/월 구조 내의 파일도 모두 탐색
        for arch in ARCHIVE_DIR.rglob("*.json"):
            if len(arch.stem) == 10:
                all_dates.append(arch.stem)
                _copy_if_changed(arch, DOCS_DATA_DIR / arch.name)

    all_dates = sorted(list(set(all_dates)), reverse=True)
    if not all_dates: return

    expected_files = {f"{d_str}.json" for d_str in all_dates}
    for existing in DOCS_DATA_DIR.glob("*.json"):
        if existing.name not in expected_files:
            existing.unlink()

    dates_js_content = "window.AVAILABLE_DATES = " + json.dumps(all_dates) + ";\n"
    _write_if_changed(DOCS_DIR / "data" / "dates.js", dates_js_content)

    with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
        members_data = json.load(f).get("members", [])

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
    team_colors = {team: get_team_topbar_color(team, team_color_cache) for team in sorted(all_team_names)}
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
