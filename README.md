# Synergy

스타 방송 통계를 보여주는 정적 웹사이트 저장소입니다. 통계의 원본과 날짜 목록은 공유 Supabase에서 직접 읽으며, 수집과 계산은 `ststat`가 담당합니다.

## 웹 빌드

```powershell
python -m pip install -r requirements.txt
python scripts/convert_members.py
python scripts/generate_pages.py
python scripts/write_supabase_browser_config.py
```

`data/members.json`은 팀 색상과 정적 셸 생성에 쓰는 빌드 캐시입니다. 일별 수치와 성별·생일을 포함한 프로필 데이터는 `daily_member_stats`에서 읽습니다.

GitHub Actions는 수동 실행과 관련 소스 변경으로 웹을 다시 빌드합니다. 운영 주기 실행은 외부 크론이 `workflow_dispatch`를 호출합니다. 필요한 설정은 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_PUBLISHABLE_KEY`입니다.

공유 DB 스키마와 공개 권한은 `ststat/migrations`에서 관리합니다.

## 대학 로고

- 원본은 `assets/logos/<대학 이름>.webp`에 둡니다. 새 대학 로고는 여기에 파일만 넣으면 됩니다(이름이 DB 소속과 같아야 함).
- 빌드가 원본을 작게 줄여 `docs/logos/`에 싣고(화면에는 최대 28px), 카드 윗줄 색도 원본에서 뽑습니다.
- 로고가 없는 대학(신생 등)은 이미지를 요청하지 않고 대학 색 바탕에 이름 첫 글자를 보여 줍니다.
