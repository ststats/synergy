# STSTATS

스타 방송을 하는 스트리머들의 통계를 자동으로 모아서 보여주는 사이트.

## 뭘 보여주나

- **별풍선 / 방송시간 / 누적시청자** — 풍고(poonggo.com) API
- **스폰전적(스폰판수)** — EloBoard(eloboard.co.kr) API

매일 자동으로 수집해서 세 종류 페이지로 보여줌:

| 페이지 | 내용 |
|---|---|
| 전체 페이지 | 모든 팀 비교 그리드 + FA 명단 |
| 팀별 페이지 | 팀 하나만 떼서 보는 화면 |
| 개인 프로필 | 인원별 성별/생일/소속/직책/종족/티어 + 지표 4개, 방송 중이면 실시간 임베드 표시 |

날짜/지표는 상단에서 즉시 전환(새로고침 없음).

## 파일 구조

```
├── requirements.txt
├── data/
│   ├── members.json          ← (자동생성) StarUniv Supabase tier_members로부터 변환
│   ├── latest.json           ← (자동생성) 오늘자 수집 결과
│   └── archive/YYYY/MM/YYYY-MM-DD.json  ← (자동생성) 날짜별 스냅샷(연/월 하위 폴더)
├── scripts/
│   ├── _common.py                  ← 공용 유틸(HTTP 재시도, 날짜/파일 처리 등)
│   ├── supabase_roster.py          ← StarUniv Supabase 로스터 읽기/쓰기
│   ├── convert_members.py          ← Supabase tier_members -> json 변환
│   ├── sync_members.py             ← EloBoard 티어 API로 신규 멤버 자동 추가
│   ├── fetch_poonggo_data.py       ← 풍고 API 수집(순수 함수)
│   ├── fetch_eloboard_data.py      ← EloBoard API 수집(순수 함수)
│   ├── update_data.py              ← 위 둘을 조율하는 메인 오케스트레이터
│   ├── generate_pages.py           ← 페이지 HTML 생성(Jinja2로 templates/ 조립)
│   ├── subset_font.py              ← 폰트 서브셋 생성
│   ├── fix_august_sponsor.py       ← 특정 달 스폰전적 결측 사고 시 재사용하는 일회용 스크립트
│   └── templates/
│       ├── page.html.j2  ← HTML 뼈대
│       ├── style.css     ← 전체 CSS
│       ├── mobile.css    ← 모바일 CSS
│       └── app.js.j2     ← 클라이언트 JS 전체
├── docs/                      ← GitHub Pages가 서빙하는 실제 사이트
│   ├── index.html / team.html / profile.html
│   ├── data/daily/YYYY-MM-DD.json  ← 브라우저가 fetch하는 원본 데이터
│   ├── fonts/PretendardVariable.woff2
│   └── logos/{팀이름}.webp
└── .github/workflows/update-stats.yml  ← 전체 갱신 파이프라인
```

## 데이터가 도는 방식

- 로스터 원본은 **StarUniv와 같은 Supabase의 `tier_members`**입니다 — StarUniv 관리자에서 수정하면 이 프로젝트의 다음 실행 때 반영됩니다
- GitHub Actions가 돌면서: Supabase 로스터 동기화 → 풍고/EloBoard 수집 → 날짜별 아카이브 저장 → 사이트 페이지 생성
- 달이 바뀌면 지난달 데이터를 한 번 더 재조회해서 확정치로 갱신
- 사람 정보(팀/티어 등)를 나중에 고치면, `tier_members.modified_at`의 날짜를 기준으로 과거 기록까지 소급 정정 가능 — 정정이 끝나면 자동으로 비워집니다
- EloBoard에 새 선수가 나타나면 정식 로스터에 바로 넣지 않고 `tier_member_candidates`에 대기 후보로 저장합니다

## 그 외 기능

- 팀 로고 자동 표시, 팀별 순위 + 전달 대비 변동(▲▼)
- 상위 1%/5%/10% 배지, 생일(🎂) 표시
- 수장/전력외는 빨간 배경으로 표시, 값이 0인 멤버와 함께 팀 합계·평균·순위 집계에서 제외
- 개인 프로필에서 방송 중인 인원은 실시간 임베드(썸네일+LIVE 배지+시청자수) 표시, 방송 중 아니면 자동으로 숨김
- 모바일 대응, 자체 호스팅 폰트(서브셋으로 용량 최소화)


## Supabase 연결

처음 적용할 때는 [`SUPABASE_LINK_SETUP.md`](SUPABASE_LINK_SETUP.md)의 SQL/Actions Secret 설정을 먼저 진행하세요.

## 수동 갱신

저장소 → Actions → **Update all stats** → Run workflow


