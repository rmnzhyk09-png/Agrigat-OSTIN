-- Индексы для ускорения поиска по db_records (2.1M+ строк).
-- Как выполнить: Supabase Dashboard → SQL Editor → New query → вставь → Run.
-- Без этих индексов ilike по text/author делает полный скан 2.1M строк
-- и упирается в timeout (HTTP 500 PGRST105 → бот падает в локальное зеркало).

-- 1) pg_trgm для нечёткого поиска подстрок (%query%) по text и author.
--    Это включает и русский текст (ilike).
create extension if not exists pg_trgm;

create index if not exists idx_db_records_text_trgm
  on public.db_records using gin (text gin_trgm_ops);

create index if not exists idx_db_records_author_trgm
  on public.db_records using gin (author gin_trgm_ops);

create index if not exists idx_db_records_source
  on public.db_records (source text_pattern_ops);

-- 2) Профили: быстрый поиск по ФИО/телефону уже покрыт
--    idx_profiles_full_name (tsvector) + idx_profiles_phones (gin),
--    но добавим trgm на full_name для мягкого ilike-поиска.
create index if not exists idx_profiles_full_name_trgm
  on public.db_profiles using gin (full_name gin_trgm_ops);

-- 3) Объём расширен — пересчёт статистики для планировщика.
analyze public.db_records;
analyze public.db_profiles;