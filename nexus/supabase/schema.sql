-- Nexus — Supabase schema
-- Run this in your Supabase SQL editor (Dashboard → SQL → New query)

create table if not exists public.chats (
  id uuid primary key,
  user_id uuid not null,
  title text not null default 'New chat',
  model text,
  messages jsonb not null default '[]'::jsonb,
  pinned boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- If you installed an older version of this schema, run this once instead:
-- alter table public.chats add column if not exists pinned boolean not null default false;

-- Index for fast per-user listing
create index if not exists chats_user_updated_idx
  on public.chats (user_id, updated_at desc);

-- user_id defaults to the authenticated caller
alter table public.chats alter column user_id set default auth.uid();

-- Row Level Security: users can only see/touch their own chats. Requires
-- auth (e.g. magic-link email) to be enabled — see supabase/migrations for
-- upgrading an existing project that used the old anon-mode policies.
alter table public.chats enable row level security;

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
