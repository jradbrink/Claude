-- Black Box V1.5 — real oil temperature from the internal CAN bus.
-- warmup_s keeps meaning "time until fully warm per the criterion in effect";
-- the criterion applied per trip is recorded in warm_criterion.

alter table trips add column if not exists coolant_warmup_s int;
alter table trips add column if not exists oil_warmup_s int;
alter table trips add column if not exists max_oil_temp_c numeric(5, 1);
alter table trips add column if not exists warm_criterion text not null default 'coolant';

alter table cold_events add column if not exists oil_c_at_start numeric(5, 1);
