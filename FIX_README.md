# Synergy Supabase runtime fix

실제 원인:
- `scripts/templates/app.js.j2`가 `(function () { ... })()` 일반 함수였는데
  내부에서 `await loadAvailableDates()`를 사용하고 있었습니다.
- 브라우저에서는 `await is only valid in async functions...` SyntaxError가 나므로
  Supabase 요청 자체가 실행되지 않았습니다.
- async로만 바꾸면 await 동안 DOMContentLoaded가 먼저 끝날 수 있어,
  초기화 함수에 `document.readyState` 확인도 추가했습니다.
- Supabase 조회가 실패하면 이제 빈 화면 대신 실제 오류 메시지를 화면에 표시합니다.

적용:
1. ZIP을 Synergy 프로젝트 루트에 덮어쓰기
2. `git add -A`
3. `git commit -m "Fix Synergy Supabase runtime"`
4. `git pull --rebase origin main`
5. `git push origin main`

수정 파일:
- scripts/templates/app.js.j2
- docs/index.html
- docs/team.html
- docs/profile.html
