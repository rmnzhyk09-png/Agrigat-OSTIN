-- Таблица профилей: структурированные данные «пробива» человека.
-- Создаётся автоматически при первом запуске, но можно выполнить вручную.

create table if not exists public.db_profiles (
  id uuid primary key default gen_random_uuid(),

  -- ФИО и личные данные
  full_name text,                 -- ФИО (основное)
  name_variants jsonb,            -- ["Иванов И.И.", "Иванов-Петров"] другие написания
  surname text,
  first_name text,
  patronymic text,
  maiden_name text,               -- девичья фамилия
  gender text,                    -- male/female
  date_of_birth text,             -- ДР (текст: "1990-01-15" или "1990")
  age integer,
  citizenship text,               -- гражданство
  nationality text,               -- национальность
  place_of_birth text,            -- место рождения

  -- Документы
  passport_series text,
  passport_number text,
  passport_issued_by text,        -- кем выдан
  passport_issue_date text,       -- когда выдан
  passport_valid_until text,      -- срок действия
  inn text,                       -- ИНН
  snils text,                     -- СНИЛС
  ogrnip text,                    -- ОГРНИП (если ИП)
  driver_license text,            -- водительское удостоверение
  driver_license_categories text, -- категории
  military_id text,               -- военный билет

  -- Адреса
  registration_address text,      -- прописка (полный)
  registration_postal_code text,
  actual_address text,            -- фактическое проживание
  temp_registration text,         -- временная регистрация
  address_history jsonb,          -- история адресов

  -- Контакты
  phones jsonb,                   -- ["+79001234567", "+79112223344"] нормализованные
  phone_operators jsonb,          -- {"+79001234567": "МТС", "+79112223344": "Билайн"}
  emails jsonb,                   -- ["ivan@mail.ru"]
  telegram jsonb,                 -- {"username": "@ivan", "url": "https://t.me/ivan", "groups": [...]}
  whatsapp jsonb,
  viber jsonb,
  social_handles jsonb,           -- {"vk": "id123", "instagram": "@ivan", ...}
  nicknames jsonb,                -- ["ivan_dev", "ivan123"]

  -- Связи и окружение
  family_status text,             -- семейное положение
  spouse jsonb,                   -- ФИО супруга/и (+ бывшие)
  children jsonb,                 -- [{name, dob, age}]
  parents jsonb,                  -- [{name, relation}]
  siblings jsonb,
  relatives jsonb,                -- [{name, relation, phone}]
  business_partners jsonb,        -- [{name, role, company}]
  colleagues jsonb,               -- [{name, company, position}]
  social_connections jsonb,       -- [{name, platform, type}]

  -- Соцсети и онлайн
  vk_url text,
  vk_friends_count integer,
  vk_photos_count integer,
  vk_groups jsonb,
  vk_last_active text,
  ok_url text,
  instagram_url text,
  facebook_url text,
  twitter_url text,
  linkedin_url text,
  tiktok_url text,
  youtube_url text,
  dating_profiles jsonb,          -- [{platform, url, last_active}]
  forum_profiles jsonb,           -- [{site, url, username}]
  marketplace_profiles jsonb,     -- [{site, url, rating}]

  -- Недвижимость
  real_estate jsonb,              -- [{address, area_m2, cadastral_no, type, value, mortgage, owner_role}]
  real_estate_total_count integer,
  real_estate_total_value numeric,

  -- Транспорт (ГИБДД)
  vehicles jsonb,                 -- [{make, model, year, vin, plate, color}]
  driver_license_status text,
  traffic_violations_count integer,
  dtp_history jsonb,              -- [{date, role, description}]
  vehicle_search_status text,     -- в розыске / нет

  -- Суды и приставы
  court_cases jsonb,              -- [{court, case_no, role, subject, amount, date, status}]
  court_cases_count integer,
  court_debt_total numeric,       -- сумма по судебным долгам
  enforcement_proceedings jsonb,  -- [{fssp_no, debtor, amount, status, date}]
  enforcement_debt_total numeric,
  criminal_cases jsonb,           -- [{case_no, article, status, date}]
  criminal_record boolean default false,
  administrative_offences jsonb,  -- [{article, fine, date, status}]
  wanted_status text,             -- в розыске / нет
  sanctions_restrictions jsonb,   -- [{type, reason, date}]

  -- Финансы
  tax_debts jsonb,                -- [{amount, period, fns_code}]
  tax_debt_total numeric,
  bankruptcy_status text,         -- банкрот / в процедуре / нет
  credit_defaults jsonb,          -- [{bank, amount, date}]
  account_arrests jsonb,          -- [{account_type, amount, date}]
  crypto_wallets jsonb,           -- [{type, address, balance}]

  -- Работа и бизнес
  current_employer text,
  employer_inn text,
  employer_ogrn text,
  position text,
  career_history jsonb,           -- [{company, position, period, inn}]
  businesses jsonb,               -- [{name, inn, ogrn, role, status}]

  -- Военное / госслужба
  military_status text,           -- призыв / запас / отсрочка
  government_positions jsonb,     -- [{position, org, period}]
  income_declarations jsonb,      -- [{year, income, property}]

  -- Репутация
  media_mentions jsonb,           -- [{title, url, date, sentiment}]
  reviews jsonb,                  -- [{platform, text, rating, date}]
  complaint_count integer,

  -- Цифровой след
  data_breaches jsonb,            -- [{source, date, data_types}]
  stealer_logs_detected boolean default false,
  pastes jsonb,                   -- [{url, date}]

  -- Реестры и статусы
  efrsb_status text,              -- ЕФРСБ: банкрот / нет
  egrul_roles jsonb,              -- [{company, role, inn, status}]
  disqualified boolean default false,
  exit_ban boolean default false, -- ограничение на выезд
  tax_evasion_list boolean default false,
  cb_blacklist boolean default false,

  -- Мета
  source_files jsonb,             -- ["file1.csv", "export.xlsx"] откуда данные
  import_ids jsonb,               -- [1, 3, 7] какие импорты содержат данные
  overall_confidence text,        -- high/medium/low — насколько полны данные
  completeness_score numeric,     -- 0.0–1.0 заполненность полей
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Индексы для быстрого поиска по ФИО / телефону / email
create index if not exists idx_profiles_full_name on public.db_profiles using gin (to_tsvector('russian', coalesce(full_name, '')));
create index if not exists idx_profiles_surname on public.db_profiles (surname);
create index if not exists idx_profiles_inn on public.db_profiles (inn);
create index if not exists idx_profiles_snils on public.db_profiles (snils);
create index if not exists idx_profiles_passport on public.db_profiles (passport_series, passport_number);
create index if not exists idx_profiles_phones on public.db_profiles using gin (phones);
create index if not exists idx_profiles_emails on public.db_profiles using gin (emails);
create index if not exists idx_profiles_vehicles on public.db_profiles using gin (vehicles);
create index if not exists idx_profiles_court on public.db_profiles (court_cases_count);
create index if not exists idx_profiles_debt on public.db_profiles (enforcement_debt_total);

alter table public.db_profiles enable row level security;
