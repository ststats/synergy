"""시너지 웹 빌드 검사: 스크립트에 박는 값의 escape, 페이지 렌더, 티어 순서."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_pages  # noqa: E402
from _common import validate_and_clean_members  # noqa: E402

# 공통 티어 사다리의 사본. 스타유니브 templates/assets/core.js의 SITE_ORDER.tiers, ststat
# processors/staruniv_ranking.py의 TIER_ORDER와 같아야 한다 - 이 테스트는 다른 저장소를 읽지 않으므로,
# 사다리를 바꿀 때는 세 곳과 이 사본을 함께 고친다.
TIER_ORDER = ['갓', '킹', '잭', '조커', '스페이드', '0', '1', '2', '3', '4', '5', '6', '7', '8', '베이비']


def test_json_for_script_cannot_close_script_tag():
    value = {"team": '</script><script>alert(1)</script>', "nick": "a b&c"}
    out = generate_pages.json_for_script(value)
    assert "<" not in out and ">" not in out and "&" not in out
    assert " " not in out
    assert json.loads(out) == value


def test_team_name_with_quotes_renders_as_js_literal():
    team = 'A"대\\학\n'
    html = generate_pages.generate_html("팀별 현황", team, False, "", {team: "#000000"})
    assert generate_pages.json_for_script(team) in html
    assert "</script><script>" not in html


def test_all_pages_render():
    colors = {"테스트대": "#123456", "FA": "#8b8f99", "휴면": "#8b8f99"}
    for args in [("시너지", "", False), ("팀별 현황", "테스트대", False), ("프로필", "", True)]:
        html = generate_pages.generate_html(*args, "", colors)
        assert html.lstrip().lower().startswith("<!doctype html")
        assert "supabase-config.js" in html


def test_tier_order_matches_shared_ladder_copy():
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const TIER_ORDER = \[(.*?)\];", src)
    assert m, "app.js에서 TIER_ORDER를 찾지 못함"
    assert re.findall(r"'([^']*)'", m.group(1)) == TIER_ORDER


def test_validate_and_clean_members_drops_incomplete_rows():
    rows = [
        {"id": " abc ", "nickname": "닉", "elo_id": "12.0"},
        {"id": "", "nickname": "아이디 없음"},
        {"id": "x", "nickname": ""},
        "잘못된 행",
    ]
    cleaned = validate_and_clean_members(rows)
    assert [(m["id"], m["elo_id"]) for m in cleaned] == [("abc", 12)]


def test_daily_queries_fetch_only_used_columns():
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    full = re.search(r"const DAILY_COLUMNS = '([^']+)'", src).group(1).split(",")
    rank = re.search(r"const DAILY_RANK_COLUMNS = '([^']+)'", src).group(1).split(",")
    # 화면에서 안 쓰는 칸은 받지 않는다
    assert "month_start" not in full and "elo_id" not in full
    # 목록 화면은 생일 달만 쓴다: 생년월일·종족은 받지 않는다(공개 권한도 없다)
    assert "birth_month" in full and "birth_date" not in full and "race" not in full
    # 개인 페이지(휴면 선수 포함)는 한 사람용 함수로 읽는다
    assert "client.rpc('player_profile_stats'" in src and "client.rpc('player_live'" in src
    # 지난달 순위 계산용은 소속·직책·성별·지표만(생일·닉네임 등 개인 정보는 받지 않는다)
    assert set(rank) == {"role", "affiliation", "gender", "balloons", "broadcast_seconds",
                         "cumulative_viewers", "sponsor_wins", "sponsor_losses"}
    assert "loadDailyData(prevDate, { light: true })" in src


def test_pages_get_csp_header_and_have_no_inline_code():
    """CSP(Vercel 응답 헤더)로 인라인 스크립트를 막으므로 페이지에 on*="..." 속성이나 실행되는 인라인 <script>가 없어야 한다.
    CSP를 <meta>로 두면 Chrome이 미리 읽기를 꺼서 파일을 차례로 받으므로 페이지에는 두지 않는다."""
    vercel = json.loads((ROOT / "docs" / "vercel.json").read_text(encoding="utf-8"))
    routes = [r for r in vercel["routes"] if "script-src" in r.get("headers", {}).get("Content-Security-Policy", "")]
    assert len(routes) == 1
    csp = routes[0]["headers"]["Content-Security-Policy"]
    assert "unsafe-inline" not in re.search(r"script-src ([^;]+)", csp).group(1)
    assert "frame-ancestors 'self'" in csp
    page_src = re.compile(routes[0]["src"])
    for url in ["/", "/index.html", "/profile.html", "/team.html"]:
        assert page_src.match(url), url
    for url in ["/app.js", "/style.css", "/logos/a.webp", "/nope.html"]:
        assert not page_src.match(url), url
    colors = {"테스트대": "#123456", "FA": "#8b8f99", "휴면": "#8b8f99"}
    for args in [("시너지", "", False), ("팀별 현황", "테스트대", False), ("프로필", "", True)]:
        html = generate_pages.generate_html(*args, "", colors)
        assert "Content-Security-Policy" not in html
        assert not re.search(r"\son[a-z]+\s*=", html)
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag or 'type="application/ld+json"' in tag, tag
    app = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+=[\"']", app)


def test_font_subset_includes_app_js_text():
    """app.js가 그려 넣는 문구(불러오기 안내 등) 글자도 글꼴에 넣는다(빠지면 그 글자만 다른 글꼴로 보인다)."""
    src = (ROOT / "scripts" / "subset_font.py").read_text(encoding="utf-8")
    assert 'DOCS_DIR.glob("*.js")' in src


def test_first_render_does_not_wait_for_date_list():
    """최신(또는 주소의) 통계를 받았으면 날짜 목록을 기다리지 않고 그린다(목록은 달력·순위 비교용)."""
    src = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
    assert "const latestFirst = latestPrefetch ? await latestPrefetch : (urlDatePrefetch ? await urlDatePrefetch : null);" in src
    assert "AVAILABLE_DATES = [latestFirst.date];" in src
