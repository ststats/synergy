-- STSTATS/Synergy가 StarUniv와 같은 Supabase 로스터를 공유할 때 필요한 추가 테이블.
-- StarUniv의 supabase/setup.sql을 이미 실행한 같은 프로젝트에서 이 파일을 1회 실행하세요.
-- tier_members 자체는 기존 StarUniv 테이블을 그대로 사용합니다.

create table if not exists public.tier_member_candidates (
  id text primary key,
  nickname text not null default '',
  elo_id integer,
  gender text,
  race text,
  tier text,
  affiliation text,
  source text,
  found_at date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists tier_member_candidates_elo_id_idx
  on public.tier_member_candidates (elo_id);
create index if not exists tier_member_candidates_found_at_idx
  on public.tier_member_candidates (found_at desc);

alter table public.tier_member_candidates enable row level security;

-- 브라우저 공개 조회 대상이 아니다. GitHub Actions의 service role만 사용한다.
revoke all on table public.tier_member_candidates from anon, authenticated;
grant select, insert, update, delete on table public.tier_member_candidates to service_role;
