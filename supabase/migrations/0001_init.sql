-- Black Box V1 — initial schema
-- Apply with: supabase db push   (or paste into the SQL editor)

create extension if not exists pgcrypto;

create table if not exists vehicles (
    id           uuid primary key default gen_random_uuid(),
    display_name text not null,
    vin          text unique,
    make         text,
    model        text,
    model_year   int,
    created_at   timestamptz not null default now()
);

create table if not exists devices (
    id           uuid primary key,
    vehicle_id   uuid references vehicles (id),
    name         text,
    last_seen_at timestamptz,
    created_at   timestamptz not null default now()
);

create table if not exists trips (
    id                   uuid primary key,
    vehicle_id           uuid not null references vehicles (id),
    device_id            uuid references devices (id),
    started_at           timestamptz not null,
    ended_at             timestamptz not null,
    duration_s           int not null check (duration_s >= 0),
    distance_km_est      numeric(7, 2) not null default 0,
    warmed_up            boolean not null default false,
    warmup_s             int,
    max_rpm              int not null default 0,
    max_coolant_c        numeric(5, 1),
    cold_violation_count int not null default 0,
    source               text not null default 'pi',
    created_at           timestamptz not null default now()
);

create index if not exists trips_vehicle_started_idx
    on trips (vehicle_id, started_at desc);

create table if not exists cold_events (
    id                 uuid primary key,
    trip_id            uuid not null references trips (id) on delete cascade,
    started_at         timestamptz not null,
    ended_at           timestamptz,
    duration_s         numeric(6, 1),
    max_rpm            int not null,
    coolant_c_at_start numeric(5, 1)
);

create index if not exists cold_events_trip_idx on cold_events (trip_id);

-- RLS is enabled from day one. The V1 device syncs with the service-role key,
-- which bypasses RLS; these policies matter once a dashboard/app with anon or
-- user JWTs is added. No permissive anon policies are created on purpose.
alter table vehicles    enable row level security;
alter table devices     enable row level security;
alter table trips       enable row level security;
alter table cold_events enable row level security;
