-- Shareable read-only "vehicle CV" links (web/share.html).
--
-- Design: anon still has ZERO direct table access. The only public surface
-- is the RPC public_share(token), a SECURITY DEFINER function that returns
-- a JSON snapshot for a valid, non-revoked token. Links are created and
-- revoked from the dashboard by the logged-in owner. Revoking (or deleting)
-- a link kills the URL immediately.

create table if not exists share_links (
    token      text primary key default encode(gen_random_bytes(16), 'hex'),
    vehicle_id uuid not null references vehicles (id),
    label      text,
    created_at timestamptz not null default now(),
    revoked    boolean not null default false
);

alter table share_links enable row level security;

create policy "authenticated_manage_share_links"
    on share_links for all to authenticated using (true) with check (true);

create or replace function public_share(share_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_link    share_links%rowtype;
    v_vehicle vehicles%rowtype;
begin
    select * into v_link
    from share_links
    where token = share_token and not revoked;
    if not found then
        return null;
    end if;

    select * into v_vehicle from vehicles where id = v_link.vehicle_id;

    return jsonb_build_object(
        'vehicle', jsonb_build_object(
            'display_name', v_vehicle.display_name,
            'vin', v_vehicle.vin,
            'make', v_vehicle.make,
            'model', v_vehicle.model,
            'model_year', v_vehicle.model_year
        ),
        'generated_at', now(),
        'trips', coalesce((
            select jsonb_agg(jsonb_build_object(
                'started_at', t.started_at,
                'duration_s', t.duration_s,
                'distance_km_est', t.distance_km_est,
                'warmed_up', t.warmed_up,
                'warmup_s', t.warmup_s,
                'coolant_warmup_s', t.coolant_warmup_s,
                'oil_warmup_s', t.oil_warmup_s,
                'max_rpm', t.max_rpm,
                'max_coolant_c', t.max_coolant_c,
                'max_oil_temp_c', t.max_oil_temp_c,
                'cold_violation_count', t.cold_violation_count
            ) order by t.started_at desc)
            from trips t
            where t.vehicle_id = v_link.vehicle_id
        ), '[]'::jsonb)
    );
end;
$$;

revoke all on function public_share(text) from public;
grant execute on function public_share(text) to anon, authenticated;
