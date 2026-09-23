-- Part 8 Synergy browser read.
-- Run after ststat migration 005_synergy_daily_stats.sql.

alter table public.daily_member_stats enable row level security;

drop policy if exists synergy_daily_public_read on public.daily_member_stats;
create policy synergy_daily_public_read on public.daily_member_stats
for select to anon, authenticated using (true);

grant select on public.daily_member_stats to anon, authenticated;

create or replace view public.synergy_daily_dates as
select stat_date, max(updated_at) as updated_at
from public.daily_member_stats
group by stat_date
order by stat_date desc;

grant select on public.synergy_daily_dates to anon, authenticated;
