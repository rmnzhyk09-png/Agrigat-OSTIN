-- Скрипт для создания таблиц импорта БД в Supabase.
-- Как выполнить: Supabase Dashboard → SQL Editor → New query → вставь → Run.
-- Либо бот создаст их сам, если задать SUPABASE_PROJECT_REF + SUPABASE_ACCESS_TOKEN.

create table if not exists public.db_imports (
  id bigint generated always as identity primary key,
  file_name text,
  format text,
  rows_total integer default 0,
  sections_found integer default 0,
  sections_new integer default 0,
  created_at timestamptz default now()
);

create table if not exists public.db_sections (
  id bigint generated always as identity primary key,
  name text unique not null,
  created_at timestamptz default now()
);

-- Расширение для gen_random_uuid обычно уже включено в Supabase;
-- если нет — выполни: create extension if not exists "pgcrypto";
create table if not exists public.db_records (
  id uuid primary key default gen_random_uuid(),
  import_id bigint,
  section text not null,
  source text,
  author text,
  text text,
  url text,
  date text,
  checksum text unique not null,
  raw jsonb,
  created_at timestamptz default now()
);

create index if not exists idx_db_records_section on public.db_records (section);
create index if not exists idx_db_records_import on public.db_records (import_id);

alter table public.db_records enable row level security;
alter table public.db_sections enable row level security;
alter table public.db_imports enable row level security;

-- Доступ на запись через service_role (по умолчанию обходит RLS).
-- Если хочешь открыть чтение анонимам/авторизованным — добавь политики:
-- create policy "read_all" on public.db_records for select using (true);