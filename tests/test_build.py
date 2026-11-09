"""시너지 웹 빌드 검사: 스크립트에 박는 값의 escape, 페이지 렌더, 티어 순서."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_pages  # noqa: E402


def test_json_for_script_cannot_close_script_tag():
    value = {"team": "</script><script>alert(1)</script>", "nick": "a b&c"}
    out = generate_pages.json_for_script(value)
    assert "<" not in out and ">" not in out and "&" not in out
    assert " " not in out
    assert json.loads(out) == value


# 빌드가 만드는 세 페이지(main과 같은 인자)
PAGES = [("시너지", {}), ("팀별 현황", {"is_team": True}), ("프로필", {"is_profile": True})]


def render(title, kwargs):
    return generate_pages.generate_html(title, **kwargs)


def test_all_pages_render():
    for title, kwargs in PAGES:
        html = render(title, kwargs)
        assert html.lstrip().lower().startswith("<!doctype html")
        assert "supabase-config.js" in html
        config = re.search(r'<script type="application/json" id="page-config">(.*?)</script>', html).group(1)
        assert json.loads(config) == {
            "isProfile": bool(kwargs.get("is_profile")),
            "isTeam": bool(kwargs.get("is_team")),
        }


def test_tier_order_comes_from_db_ladder():
    """티어 순서 원본은 DB tier_ladder다. app.js는 사본 없이 api_daily_stats의 tier_rank로 정렬한다."""
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert "TIER_ORDER" not in src
    assert "tier_rank: r.tier_rank ?? null" in src  # 받은 값을 화면용 멤버로 옮겨야 정렬에 쓰인다
    assert "(a.tier_rank ?? Infinity) - (b.tier_rank ?? Infinity) ||" in src  # 티어 → 종족 → 이름순


def test_build_uses_only_public_key():
    """빌드는 서비스 키 없이 공개 키만 쓴다(페이지 설정, 글꼴에 넣을 선수 이름은 공개 읽기 함수로)."""
    wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "SERVICE_ROLE" not in wf and "ANON_KEY" not in wf
    font = (ROOT / "scripts" / "subset_font.py").read_text(encoding="utf-8")
    assert "/rest/v1/rpc/{name}" in font
    assert '_rpc("api_daily_stats")' in font and '_rpc("elo_players_list")' in font
    for script in (ROOT / "scripts").glob("*.py"):
        assert "/rest/v1/tier_members" not in script.read_text(encoding="utf-8"), script.name


def test_page_reads_only_public_functions_not_tables():
    """표는 읽지 않고 공개 읽기 함수(api_*·player_*·elo_players_list)만 부른다. 열 구성은 ststat 쪽 함수가 정한다."""
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert ".from(" not in src.replace("Array.from(", "")
    assert "from(table)" not in src
    allowed = {
        "api_live_ids",
        "api_stats_dates",
        "api_daily_stats",
        "api_university_logos",
        "player_profile_stats",
        "player_live",
        "elo_players_list",
    }
    assert set(re.findall(r"\.rpc\('([a-z_]+)'", src)) == allowed
    assert "client.rpc('player_profile_stats'" in src and "client.rpc('player_live'" in src
    assert "p_light: light && !latest ? true : null" in src
    assert "loadDailyData(prevDate, { light: true })" in src


def test_pages_get_csp_header_and_have_no_inline_code():
    """CSP(Vercel 헤더)가 인라인 스크립트를 막으므로 on* 속성·인라인 <script>가 없어야 한다.
    <meta> CSP는 Chrome의 미리 읽기를 꺼서 두지 않는다."""
    vercel = json.loads((ROOT / "docs" / "vercel.json").read_text(encoding="utf-8"))
    routes = [r for r in vercel["routes"] if "script-src" in r.get("headers", {}).get("Content-Security-Policy", "")]
    assert len(routes) == 1
    csp = routes[0]["headers"]["Content-Security-Policy"]
    assert "unsafe-inline" not in re.search(r"script-src ([^;]+)", csp).group(1)
    assert "frame-ancestors 'self'" in csp
    page_src = re.compile(routes[0]["src"])
    for url in ["/", "/index.html", "/profile.html", "/team.html", "/profile", "/team"]:
        assert page_src.match(url), url
    for url in ["/app.js", "/style.css", "/logos/a.webp", "/nope.html", "/teams", "/index"]:
        assert not page_src.match(url), url
    for title, kwargs in PAGES:
        html = render(title, kwargs)
        assert "Content-Security-Policy" not in html
        assert not re.search(r"\son[a-z]+\s*=", html)
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag or 'type="application/ld+json"' in tag, tag
    app = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+=[\"']", app)


def test_font_subset_includes_app_js_text():
    """app.js가 그려 넣는 문구 글자도 글꼴에 넣는다."""
    src = (ROOT / "scripts" / "subset_font.py").read_text(encoding="utf-8")
    assert 'DOCS_DIR.glob("*.js")' in src
    assert 'not p.name.endswith(".min.js")' in src


def test_png_button_only_on_table_pages_and_library_is_copied():
    """이미지 저장 버튼은 전체·팀 페이지에만 두고, html2canvas는 docs에 복사한다."""
    index, team, profile = (render(*page) for page in PAGES)
    assert 'id="png-btn"' in index and 'id="png-btn"' in team
    assert 'id="png-btn"' not in profile
    assert generate_pages.HTML2CANVAS_JS.read_text(encoding="utf-8").startswith("/*!\n * html2canvas 1.4.1")
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert "s.src = 'html2canvas.min.js'" in src
    assert "windowWidth: 1280" in src


def test_rank_summary_only_on_index_page():
    """대학 순위(평균·합계)와 개인 TOP 15 순위 패널은 전체 페이지 맨 위에만 둔다."""
    assert 'id="rank-summary"' in render(*PAGES[0])
    assert 'id="rank-summary"' not in render(*PAGES[1])
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert "renderRankSummary(teamStats, people, def);" in src
    assert "const PERSON_TOP = 15;" in src and "people.slice(0, PERSON_TOP)" in src


def test_first_render_does_not_wait_for_date_list():
    """최신(또는 주소의) 통계를 받으면 날짜 목록을 기다리지 않고 그린다."""
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert (
        "let initialData = latestPrefetch ? await latestPrefetch : urlDatePrefetch ? await urlDatePrefetch : null;"
        in src
    )
    assert "AVAILABLE_DATES = [initialDate];" in src
