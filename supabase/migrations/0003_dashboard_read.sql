-- Web dashboard (web/index.html): read-only access for logged-in users.
--
-- The dashboard uses the public anon key + Supabase Auth (email/password).
-- These policies grant SELECT only, and only to authenticated sessions —
-- the anon role still sees nothing. The Pi keeps writing with the service
-- key, which bypasses RLS. Create your login under Authentication → Users
-- in the Supabase dashboard, and keep public sign-ups disabled.

create policy "authenticated_read_vehicles"
    on vehicles for select to authenticated using (true);

create policy "authenticated_read_devices"
    on devices for select to authenticated using (true);

create policy "authenticated_read_trips"
    on trips for select to authenticated using (true);

create policy "authenticated_read_cold_events"
    on cold_events for select to authenticated using (true);
