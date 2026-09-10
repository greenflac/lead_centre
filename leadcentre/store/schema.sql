-- SORP Lead Centre — схема хранилища (Supabase / Postgres).
--
-- НАКАЧЕНО: 2026-09-09 `lead_centre_initial_schema`, 2026-09-10 `scores_reason_items`
-- (колонка причин-кодов) — обе через MCP-сервер Supabase
-- (проект uskpnltyicbjlgmhskvv). Проверено после накатывания: 5 таблиц на месте, RLS
-- включён на всех, security advisors — ноль замечаний; запись и чтение живыми ключами
-- прошли (запрошено 5, записано 5, прочитано обратно 5).
--
-- Ключами из среды (SUPABASE_SECRET_KEY / SUPABASE_PUBLISHABLE_KEY) DDL выполнить нельзя,
-- нужен MCP или SQL Editor. Проверено 2026-09-09:
--   GET  /rest/v1/               → 200 (PostgREST жив)
--   GET  /rest/v1/leads          → 404 PGRST205 «Could not find the table 'public.leads'»
--   POST /rest/v1/rpc/exec_sql   → 404 PGRST202 «Could not find the function public.exec_sql»
--   GET  https://api.supabase.com/v1/projects → 401 (Management API требует отдельный PAT)
--
-- Файл идемпотентен: повторный прогон ничего не ломает — таблицы создаются через
-- `if not exists`, добавленные позже колонки — через `add column if not exists`,
-- поэтому его можно накатывать поверх уже накаченной базы.
--
-- МИГРАЦИЯ 2026-09-10 `scores.reason_items` НЕ НАКАЧЕНА (проверено в этот день):
--   GET /rest/v1/scores?select=reason_items → 400 42703 «column scores.reason_items
--       does not exist»
--   POST /rest/v1/rpc/exec_sql              → 404 PGRST202 (функции нет, DDL через
--       PostgREST невозможен)
--   GET  https://api.supabase.com/v1/projects → 401 (нужен отдельный PAT, его в среде нет)
--   MCP-сервер Supabase в сессии требует авторизации и недоступен; строки подключения
--       к Postgres (SUPABASE_DB_URL) в среде тоже нет — psql запускать не с чем.
-- Пока миграция не накатана, живое хранилище отвечает на запись оценки
-- StoreUnavailable «схема не применена» (PGRST204/42703), а не молча теряет причины.

create extension if not exists pgcrypto;

-- Обращения из каналов SORP и из внешних источников.
create table if not exists public.leads (
    id           uuid primary key default gen_random_uuid(),
    source       text        not null,   -- form | whatsapp | telegram | jivo | csv | api
    channel      text        not null,
    raw_text     text        not null,
    facts        jsonb       not null default '{}'::jsonb,
    language     text        not null default 'en',
    is_synthetic boolean     not null default false,
    received_at  date        not null default current_date,
    created_at   timestamptz not null default now()
);
create index if not exists leads_created_at_idx on public.leads (created_at desc);

-- Оценка приоритета. Одно обращение может быть переоценено новым прогоном (run_id),
-- поэтому не «одна строка на лид», а история: перезатирать оценку значит терять,
-- на чём именно сошлись модель и рубрика в прошлый раз.
create table if not exists public.scores (
    id             uuid primary key default gen_random_uuid(),
    lead_id        uuid        not null references public.leads (id) on delete cascade,
    tier           text        not null,   -- HIGH | MEDIUM | LOW | INVALID
    address_type   text        not null,
    event          text        not null,
    reasons        jsonb       not null default '[]'::jsonb,
    reason_items   jsonb       not null default '[]'::jsonb,
    evidence       jsonb       not null default '[]'::jsonb,
    violations     jsonb       not null default '[]'::jsonb,
    model          text        not null default '',
    prompt_version text        not null default '',
    usage          jsonb       not null default '{}'::jsonb,
    latency_ms     integer     not null default 0,
    run_id         text        not null default '',
    created_at     timestamptz not null default now()
);
create index if not exists scores_lead_id_idx on public.scores (lead_id);

-- Причины приоритета — данные, а не текст: `[{"code": "urgent_timeline",
-- "params": {"days": 14, "limit": 60}}, ...]`. Текст на русском и английском собирается
-- из кода и параметров в `leadcentre/engine/reasons.py`; колонка `reasons` осталась
-- готовыми русскими строками для читателей, которым хватает одного языка, но источником
-- истины быть перестала — её пишет отрисовка. Без кодов карточка, поднятая из базы,
-- не могла отрисовать английский, и интерфейс показывал бы русский текст под видом
-- английского.
-- Отдельным `alter` — чтобы файл накатывался и на уже созданную базу (create table
-- if not exists колонку в существующую таблицу не добавляет). Старые строки получают
-- '[]', и это читается как «кодов нет» — отдельный исход, а не пустой список причин.
alter table public.scores
    add column if not exists reason_items jsonb not null default '[]'::jsonb;

-- Черновик ответа.
-- lint_ok — булев, но у линтера ТРИ исхода (OK / VIOLATIONS / UNVERIFIABLE), поэтому
-- рядом стоит lint_status, а lint_ok при «не смогли проверить» = NULL (Р1). Колонки
-- lint_status в исходном ТЗ не было: без неё третий исход сворачивался бы в false,
-- то есть «не проверяли» читалось бы как «нарушение».
create table if not exists public.replies (
    id              uuid primary key default gen_random_uuid(),
    lead_id         uuid        not null references public.leads (id) on delete cascade,
    language        text        not null default 'ru',
    body            text        not null default '',
    lint_ok         boolean,
    lint_status     text        not null default 'UNVERIFIABLE',
    lint_violations jsonb       not null default '[]'::jsonb,
    status          text        not null default 'draft'
                    check (status in ('draft', 'approved', 'rejected')),
    decided_at      timestamptz,
    created_at      timestamptz not null default now(),
    constraint replies_lint_three_outcomes check (
        (lint_status = 'UNVERIFIABLE' and lint_ok is null)
        or (lint_status = 'OK'         and lint_ok is true)
        or (lint_status = 'VIOLATIONS' and lint_ok is false)
    )
);
create index if not exists replies_lead_id_idx on public.replies (lead_id);

-- Компании из внешних источников (GLEIF и далее). Ключ — пара (source, external_id):
-- один и тот же LEI из двух источников — две записи, и это осознанно.
create table if not exists public.companies (
    id                  uuid primary key default gen_random_uuid(),
    external_id         text        not null,
    source              text        not null,
    name                text        not null default '',
    city                text        not null default '',
    license_no          text,
    registrar_id        text,
    created_on          date,
    registration_status text        not null default '',
    next_renewal_on     date,
    facts               jsonb       not null default '{}'::jsonb,
    created_at          timestamptz not null default now(),
    unique (source, external_id)
);
create index if not exists companies_created_at_idx on public.companies (created_at desc);

-- Несогласие менеджера с оценкой — вход для eval (κ, confusion), а не журнал.
create table if not exists public.disagreements (
    id         uuid primary key default gen_random_uuid(),
    lead_id    uuid        not null references public.leads (id) on delete cascade,
    tier_shown text        not null default '',
    reason     text        not null,
    author     text        not null default '',
    created_at timestamptz not null default now()
);
create index if not exists disagreements_lead_id_idx on public.disagreements (lead_id);

-- RLS: пишет только сервер (secret key обходит RLS), публикуемому ключу — чтение.
-- Без включённого RLS публикуемый ключ пишет в таблицы из браузера кто угодно.
alter table public.leads          enable row level security;
alter table public.scores         enable row level security;
alter table public.replies        enable row level security;
alter table public.companies      enable row level security;
alter table public.disagreements  enable row level security;

do $$
declare t text;
begin
  foreach t in array array['leads', 'scores', 'replies', 'companies', 'disagreements'] loop
    execute format(
      'drop policy if exists %I on public.%I', 'anon_read_' || t, t);
    execute format(
      'create policy %I on public.%I for select to anon using (true)', 'anon_read_' || t, t);
  end loop;
end $$;
