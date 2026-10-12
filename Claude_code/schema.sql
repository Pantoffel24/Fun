-- Run this once in the Supabase SQL editor (safe to re-run: every statement is idempotent).

create table if not exists todos (
    id    bigint generated always as identity primary key,
    title text    not null,
    due   date,                             -- optional due date
    done  boolean not null default false
);

create table if not exists habits (
    id       bigint generated always as identity primary key,
    name     text not null unique,
    category text not null default 'Skill'
);

-- One row per habit per successful day. A missing row means a missed day.
-- The composite primary key stops a day being logged twice.
create table if not exists habit_log (
    habit_id bigint not null references habits(id) on delete cascade,
    day      date   not null,
    primary key (habit_id, day)
);

create table if not exists books (
    id           bigint generated always as identity primary key,
    title        text not null,
    author       text,
    total_pages  integer not null check (total_pages > 0),
    current_page integer not null default 0 check (current_page >= 0),
    status       text not null default 'reading' check (status in ('reading', 'finished')),
    started      date,
    finished     date
);

-- Supabase exposes every table in the public schema through its REST API, which anyone
-- holding the project URL and the publishable key can call. Enabling row level security
-- with no policies blocks that route entirely. The app connects directly as the
-- database owner, which bypasses row level security, so it is unaffected.
alter table todos      enable row level security;
alter table habits     enable row level security;
alter table habit_log  enable row level security;
alter table books      enable row level security;
