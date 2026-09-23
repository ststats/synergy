# Synergy Supabase 1000행 + 런타임 수정

실제 문제:
1. `daily_member_stats`를 한 번만 조회해 Supabase/PostgREST 기본 1000행에서 잘림.
2. 최신 소스에 일반 IIFE 안에서 `await loadAvailableDates()`를 쓰는 코드가 다시 섞여 있어 브라우저 SyntaxError 가능.
3. async 처리 후에는 DOMContentLoaded 타이밍이 지나갈 수 있어 초기화 보정 필요.

수정:
- daily_member_stats를 1000행씩 `.range()`로 끝까지 반복 조회
- async IIFE 적용
- DOM ready 상태에 따라 즉시 초기화 또는 DOMContentLoaded 대기
- legacy JSON fallback은 다시 넣지 않음

적용:
```cmd
git add -A
git commit -m "Fix Synergy Supabase pagination runtime"
git pull --rebase origin main
git push origin main
```
