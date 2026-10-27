-- Schema for the support ticket system.
-- One writer per table: see docs/architecture.md.

create extension if not exists pgcrypto;

create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    email         text not null unique,
    password_hash text not null,
    display_name  text not null,
    role          text not null default 'customer'
                  check (role in ('customer', 'agent')),
    created_at    timestamptz not null default now()
);

create table if not exists sessions (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    token_hash text not null unique,
    expires_at timestamptz not null,
    created_at timestamptz not null default now()
);
create index if not exists sessions_user_idx on sessions(user_id);

create table if not exists tickets (
    id                  uuid primary key default gen_random_uuid(),
    customer_id         uuid not null references users(id) on delete cascade,
    subject             text not null check (char_length(subject) between 1 and 200),
    body                text not null check (char_length(body) between 1 and 5000),
    status              text not null default 'open'
                        check (status in ('open', 'in_progress', 'answered', 'closed')),
    priority            int  not null default 2 check (priority between 1 and 3),
    category            text not null default 'unknown',
    needs_manual_triage boolean not null default false,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);
create index if not exists tickets_customer_idx on tickets(customer_id, created_at desc);
create index if not exists tickets_inbox_idx on tickets(status, priority, created_at);

create table if not exists messages (
    id         uuid primary key default gen_random_uuid(),
    ticket_id  uuid not null references tickets(id) on delete cascade,
    author_id  uuid not null references users(id) on delete cascade,
    direction  text not null check (direction in ('in', 'out')),
    body       text not null check (char_length(body) between 1 and 5000),
    created_at timestamptz not null default now()
);
create index if not exists messages_ticket_idx on messages(ticket_id, created_at);

-- Exactly one row per ticket (retry-safe: worker upserts on ticket_id).
create table if not exists ai_suggestions (
    ticket_id             uuid primary key references tickets(id) on delete cascade,
    category              text not null default 'unknown',
    category_confidence   real not null default 0,
    priority              int  not null default 2 check (priority between 1 and 3),
    priority_confidence   real not null default 0,
    needs_manual_triage   boolean not null default false,
    draft_text            text,
    draft_status          text not null default 'pending'
                          check (draft_status in ('pending', 'ready', 'failed')),
    model_name            text,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

-- Audit trail for every queue job. Redis is not a source of truth.
create table if not exists ai_runs (
    id          uuid primary key default gen_random_uuid(),
    ticket_id   uuid not null references tickets(id) on delete cascade,
    status      text not null default 'queued'
                check (status in ('queued', 'running', 'succeeded',
                                  'succeeded_no_draft', 'failed')),
    attempt     int  not null default 1,
    model_name  text,
    latency_ms  int,
    last_error  text,
    started_at  timestamptz,
    finished_at timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists ai_runs_ticket_idx on ai_runs(ticket_id, created_at desc);

-- Human decision on a suggestion; feeds the evaluation report.
create table if not exists suggestion_events (
    id         uuid primary key default gen_random_uuid(),
    ticket_id  uuid not null references tickets(id) on delete cascade,
    decision   text not null check (decision in ('accepted', 'edited', 'rejected')),
    edited     boolean not null default false,
    created_at timestamptz not null default now()
);
create index if not exists suggestion_events_ticket_idx on suggestion_events(ticket_id);
