# Synergy

스타 방송 통계를 보여주는 정적 웹사이트 저장소입니다. 통계의 원본과 날짜 목록은 공유 Supabase에서 직접 읽으며, 수집과 계산은 `ststat`가 담당합니다.

## 웹 빌드

```powershell
python -m pip install -r requirements.txt
python scripts/convert_members.py
python scripts/generate_pages.py
python scripts/write_supabase_browser_config.py
python -m pip install -r requirements-dev.txt
python -m pytest -q                   # tests/ (escape·페이지 렌더·티어 순서)
```

- 페이지 원본은 `templates/`(스타유니브와 같은 자리)이고, 빌드 결과 `docs/`는 저장소에 없습니다.
- 배포 때 `scripts/minify_assets.mjs`가 `docs/`의 JS·CSS·HTML에서 주석과 공백을 뺍니다(변수 이름·코드는 그대로, 원본과 구문 트리가 같을 때만). 원본의 주석은 그대로 두면 됩니다. 로컬에서 확인: `npm ci && npm run minify`.
- 페이지 스크립트는 `templates/app.js` 파일 하나이고(빌드가 `docs/app.js`로 복사), 페이지마다 다른 값은 HTML의 `<script type="application/json" id="page-config">`로 넘깁니다. CSP로 인라인 스크립트를 막으므로 `onclick="…"`·`onerror="…"`나 실행되는 인라인 `<script>`는 쓰지 않습니다(이미지 대체는 `data-fallback="hidden|none"`, 테스트가 확인).
- 티어 순서(`templates/app.js`의 `TIER_ORDER`)는 스타유니브 `core.js`의 `SITE_ORDER.tiers`, ststat `processors/staruniv_ranking.py`와 같아야 합니다(테스트가 확인).

`data/members.json`은 팀 색상과 정적 셸 생성에 쓰는 빌드 캐시입니다. 페이지의 데이터는 공유 Supabase의 **공개 읽기 함수**만
부릅니다(표는 직접 읽지 않음, 브라우저(anon)에는 표 권한이 없음 - 테스트가 확인). 함수는 화면에 나오는 칸·행만 돌려줍니다.

| 화면 | 함수 |
|---|---|
| 대학 카드 목록(날짜별·최신, 지난달 순위 계산용 가벼운 조회) | `api_daily_stats` |
| 날짜 목록 | `api_stats_dates` |
| 방송 중 점 | `api_live_ids` |
| 대학 로고·카드 색 | `api_university_logos` |
| 개인 페이지(휴면 선수 포함, 생년월일 등 프로필 칸) | `player_profile_stats`, `player_live` |

GitHub Actions는 수동 실행과 관련 소스 변경으로 웹을 다시 빌드하고, 결과(`docs/`)를 저장소에 커밋하지 않고 GitHub Pages로 바로 배포합니다(Settings → Pages → Source: GitHub Actions). Vercel 주소는 빌드하지 않고 `docs/vercel.json`이 모든 주소를 GitHub Pages로 넘깁니다(Vercel 프로젝트의 Root Directory = `docs`, 스타유니브와 같음). 운영 주기 실행은 cron-job.org가 매일 00:05·12:05(한국 시간)에 `https://api.github.com/repos/ststats/synergy/actions/workflows/build.yml/dispatches`로 `workflow_dispatch`를 호출합니다. 빌드는 먼저 테스트(`test.yml`)를 돌리고, 실패하면 배포하지 않습니다. 필요한 설정은 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_PUBLISHABLE_KEY`입니다.

공유 DB 스키마, 공개 읽기 함수와 거르는 조건(휴면 제외 등)은 `ststat/supabase/ststat.sql` 14번에서 관리합니다.
화면에 새 칸이 필요하면 그 함수를 먼저 고친 뒤 `templates/app.js`를 바꿉니다.

## 대학 로고

- 로고는 **스타유니브 어드민 → 전적 → 팀 관리**에서 올립니다(자동으로 96px로 줄이고 카드 윗줄 색도 뽑음).
  페이지가 열릴 때 공개 읽기 함수(`api_university_logos`)로 받으므로 다시 빌드할 필요가 없습니다.
- 로고가 없는 대학(신생 등)은 이미지를 요청하지 않고 대학 색 바탕에 이름 첫 글자를 보여 줍니다.
- `assets/logos/`에는 사이트 아이콘(파비콘·숲 로고) 원본만 둡니다(빌드가 줄여 `docs/logos/`에 싣음).
