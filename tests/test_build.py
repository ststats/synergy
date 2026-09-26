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
    src = (ROOT / "templates" / "app.js.j2").read_text(encoding="utf-8")
    m = re.search(r"const TIER_ORDER = \[(.*?)\];", src)
    assert m, "app.js.j2에서 TIER_ORDER를 찾지 못함"
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
