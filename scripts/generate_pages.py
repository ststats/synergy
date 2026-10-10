"""synergy 페이지 껍데기(CSR)와 정적 파일을 docs/에 만든다."""

import json
import io
import os
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
# 사이트 아이콘 원본. 대학 로고는 어드민에서 올리고 페이지가 Supabase에서 읽는다.
LOGOS_DIR = ROOT / "assets" / "logos"
WEB_LOGOS_DIR = DOCS_DIR / "logos"
WEB_LOGO_SIZE = 96
# preconnect용. SUPABASE_URL이 없으면(로컬 빌드) 태그를 넣지 않는다.
_SUPABASE = urlsplit((os.getenv("SUPABASE_URL") or "").strip())
SUPABASE_ORIGIN = f"https://{_SUPABASE.netloc}" if _SUPABASE.scheme == "https" and _SUPABASE.netloc else ""
OUTPUT_INDEX = DOCS_DIR / "index.html"
OUTPUT_PROFILE_PATH = DOCS_DIR / "profile.html"
OUTPUT_TEAM_PATH = DOCS_DIR / "team.html"

# 대학 색은 페이지가 api_university_logos로 받는다. 소속이 아닌 FA·휴면만 여기서 정한다.
FIXED_TEAM_COLORS = {"FA": "#8b8f99", "휴면": "#8b8f99"}
# JSON-LD용 사이트 주소(끝의 / 없이).
SITE_URL = os.environ.get("SITE_URL", "https://ststats.github.io/synergy").rstrip("/")
APP_JS = TEMPLATES_DIR / "app.js"
STYLE_CSS = TEMPLATES_DIR / "style.css"
HTML2CANVAS_JS = TEMPLATES_DIR / "vendor" / "html2canvas.min.js"  # 1.4.1, MIT


def json_for_script(value) -> str:
    """<script> 안에 넣어도 안전한 JSON. autoescape가 꺼져 있어 "</script>"로 블록이 끊기는 XSS를 막는다.
    U+2028/U+2029는 JS 소스에서 줄바꿈으로 취급돼 함께 escape한다."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def build_web_logos() -> None:
    """assets/logos 원본을 작은 webp로 줄여 docs/logos에 둔다."""
    WEB_LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    sources = {p.name: p for p in LOGOS_DIR.glob("*.webp")}
    for name, src in sources.items():
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


def asset_version(path: Path) -> str:
    """?v= 캐시 무효화용 내용 해시."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:10]


def generate_html(title, target_team, is_profile, logo_prefix, team_colors, team_from_url=False):
    """target_team: 팀 이름을 박아 둔 페이지(테스트용), team_from_url: 주소(?team=)로 팀을 고르는 team.html."""
    font_url = f"{logo_prefix}fonts/PretendardVariable.woff2"
    # 페이지별 값은 app.js가 읽는 JSON 블록으로 넘긴다.
    page_config = json_for_script({
        "colors": team_colors,
        "targetTeam": "" if team_from_url else (target_team or ""),
        "teamFromUrl": bool(team_from_url),
        "logoPrefix": logo_prefix,
        "isProfile": bool(is_profile),
    })
    is_team = bool(target_team or team_from_url)
    is_index = not is_profile and not is_team
    json_ld = json_for_script({"@context": "https://schema.org", "@type": "WebSite",
                               "name": "시너지", "url": f"{SITE_URL}/"}) if is_index else ""

    template = _jinja_env.get_template("page.html.j2")
    return template.render(
        app_version=asset_version(APP_JS),
        style_version=asset_version(STYLE_CSS),
        font_url=font_url,
        supabase_origin=SUPABASE_ORIGIN,
        is_profile=is_profile,
        json_ld=json_ld,
        logo_prefix=logo_prefix,
        page_config=page_config,
        is_team=is_team,
        title=title,
    )


def build_static_files() -> None:
    """app.js·style.css·html2canvas와 홈 화면 아이콘(180px PNG)을 docs에 둔다."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _write_if_changed(DOCS_DIR / "app.js", APP_JS.read_text(encoding="utf-8"))
    _write_if_changed(DOCS_DIR / "style.css", STYLE_CSS.read_text(encoding="utf-8"))
    _write_if_changed(DOCS_DIR / "html2canvas.min.js", HTML2CANVAS_JS.read_text(encoding="utf-8"))
    with Image.open(LOGOS_DIR / "파비콘.webp") as im:
        im = im.convert("RGBA")
        square = Image.new("RGBA", im.size, im.getpixel((im.width // 10, im.height // 2))[:3] + (255,))
        square.alpha_composite(im)
        buf = io.BytesIO()
        square.convert("RGB").resize((180, 180), Image.LANCZOS).save(buf, "PNG", optimize=True)
    dst = DOCS_DIR / "apple-touch-icon.png"
    if not dst.exists() or dst.read_bytes() != buf.getvalue():
        dst.write_bytes(buf.getvalue())

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
    build_web_logos()
    build_static_files()
    _write_if_changed(OUTPUT_INDEX, generate_html("시너지", "", False, "", FIXED_TEAM_COLORS))
    _write_if_changed(OUTPUT_TEAM_PATH, generate_html("팀별 현황", "", False, "", FIXED_TEAM_COLORS, team_from_url=True))
    _write_if_changed(OUTPUT_PROFILE_PATH, generate_html("프로필", "", True, "", FIXED_TEAM_COLORS))

if __name__ == "__main__":
    main()
