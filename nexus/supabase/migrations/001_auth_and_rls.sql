-- Nexus — auth + real row-level security
-- Run this in Supabase Dashboard → SQL Editor → New query, against your
-- existing project. Safe to run once; re-running is idempotent.
--
-- What this does:
--   1. Drops the old "using (true)" policies — right now ANY holder of the
--      public anon key (which ships inside the built APK) can read/edit/
--      delete every user's chats, not just their own. That's fixed here.
--   2. Requires chats.user_id to match the signed-in auth.uid() for every
--      operation, so a magic-link-authenticated user only ever touches
--      their own rows.
--   3. Defaults user_id to auth.uid() on insert so the client doesn't have
--      to (and can't spoof) another user's id.

-- Drop the old permissive policies
drop policy if exists "users read own chats" on public.chats;
drop policy if exists "users insert own chats" on public.chats;
drop policy if exists "users update own chats" on public.chats;
drop policy if exists "users delete own chats" on public.chats;

-- user_id now defaults to the authenticated caller
alter table public.chats alter column user_id set default auth.uid();

-- Strict per-user policies
create policy "users read own chats"
  on public.chats for select
  using (user_id = auth.uid());

create policy "users insert own chats"
  on public.chats for insert
  with check (user_id = auth.uid());

create policy "users update own chats"
  on public.chats for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "users delete own chats"
  on public.chats for delete
  using (user_id = auth.uid());

-- Note: any chats currently in the table under random local device IDs
-- (the old anonymous-uid scheme) are now unreachable via the API — they
-- don't match any auth.uid(). They're not deleted, just orphaned. If you
-- want to keep them, you'd need to know which browser/device wrote them
-- and re-run app-side migration (see MIGRATION.md) while signed in as the
-- account that should own them. If you don't care about old test data,
-- you can leave them or clear the table:
--   delete from public.chats where user_id not in (select id from auth.users);
