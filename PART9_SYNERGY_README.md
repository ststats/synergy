# Part 9 — Synergy 최종 컷오버

날짜 목록과 일별 통계는 이제 Supabase만 사용합니다.

적용:
1. ZIP을 Synergy 루트에 덮어쓰기
2. `CLEANUP_PART9.cmd` 실행
3. `git status`
4. `git add -A`
5. `git commit -m "Remove Synergy legacy fallbacks"`
6. `git push origin main`

삭제 대상:
- data/latest.json
- data/archive/**
- data/archive_corrections_applied.json
- data/archive_month_confirmed.json
- docs/data/daily/**
- docs/data/dates.js
- scripts/sync_members.py
- scripts/fetch_eloboard_data.py
- scripts/fetch_poonggo_data.py
- scripts/update_data.py

유지:
- data/members.json
- scripts/convert_members.py
- scripts/supabase_roster.py
- scripts/generate_pages.py
- scripts/subset_font.py
- scripts/_common.py
