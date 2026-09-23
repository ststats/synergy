# StarUniv Supabase 연동 적용법

이 프로젝트는 더 이상 Google Sheets를 로스터 원본으로 사용하지 않습니다.
StarUniv와 같은 Supabase 프로젝트의 `public.tier_members`(1,231명 로스터)를 읽어 `data/members.json`을 생성합니다.

## 1. Supabase SQL 1회 실행

StarUniv의 `supabase/setup.sql`이 이미 실행된 **같은 Supabase 프로젝트**에서 이 저장소의 `supabase_synergy.sql`을 SQL Editor로 1회 실행하세요.

이 SQL은 기존 `tier_members`를 건드리지 않고, EloBoard에서 자동 발견된 신규 후보를 검토 전까지 보관하는 `tier_member_candidates` 테이블만 추가합니다.

## 2. GitHub 저장소 설정

이 synergy/STSTATS 저장소의 **Settings → Secrets and variables → Actions**에서 다음을 설정합니다.

- Repository variable `SUPABASE_URL` = StarUniv와 같은 Supabase Project URL
- Repository secret `SUPABASE_SERVICE_ROLE_KEY` = 같은 프로젝트의 service role key

`SUPABASE_SERVICE_ROLE_KEY`는 절대 코드/README/커밋에 직접 넣지 마세요.

기존 `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_SHEET_ID` Secret은 이 프로젝트에서는 더 이상 사용하지 않습니다. 새 연동이 정상 동작하는 것을 확인한 뒤 삭제해도 됩니다.

## 3. 동작 구조

```text
StarUniv Admin
   ↓
Supabase public.tier_members
   ├─ StarUniv 사이트
   └─ 이 프로젝트 GitHub Actions
        ↓
      data/members.json
        ↓
풍고 + EloBoard 통계 수집
        ↓
기존 docs 정적 페이지 생성
```

따라서 StarUniv 관리자에서 티어표 로스터의 닉네임/팀/티어/종족/직책 등을 수정하면, 이 프로젝트는 다음 `Update all stats` 실행부터 같은 값을 사용합니다.

## 4. 신규 후보

EloBoard API에서 `tier_members`에 없는 선수를 발견하면 자동으로 정식 로스터에 넣지 않고 `public.tier_member_candidates`에 저장합니다.
검토가 끝난 후보만 StarUniv 관리자에서 `tier_members`에 정상 등록하세요.

## 5. 수동 테스트

Windows CMD:

```cmd
set SUPABASE_URL=https://YOUR_PROJECT.supabase.co
set SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
python scripts\convert_members.py --dry-run
```

1,231명 안팎이 표시되면 연결된 것입니다. 이후:

```cmd
python scripts\convert_members.py
python scripts\generate_pages.py
```

을 실행해 기존 페이지 생성이 정상인지 확인할 수 있습니다.
