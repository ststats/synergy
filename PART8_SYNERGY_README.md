# Part 8 - Synergy web cleanup

1. Overlay this patch on Synergy.
2. Run `supabase/web_cutover.sql` once in Supabase SQL Editor.
3. Add GitHub Actions variable `SUPABASE_PUBLISHABLE_KEY` (same browser/public key StarUniv uses).
4. Commit/push and run `Build Synergy web` once.
5. The browser now reads `daily_member_stats` and available dates from Supabase first.
6. Existing `docs/data/daily/*.json` and `dates.js` remain as fallback only.
7. After validation, delete files listed in `REMOVE_FILES.txt`.

The web repo no longer calls Poonggo or EloBoard and no longer calculates daily stats.
