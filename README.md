# Black Box V1

OBD-II-ansluten svart låda för entusiastbilar: kallstartsskydd med LED-indikering,
automatisk körlogg och exporterbar ägarrapport (PDF). Referensbil: Porsche 996.2 C4S.

**Läs [`docs/SPEC.md`](docs/SPEC.md) först** — arkitektur, datamodell, tillståndslogik
och beslutslistan (B1–B6).

```
docs/SPEC.md                  Teknisk specifikation (granskningsunderlag)
supabase/migrations/          Postgres-schema (vehicles, devices, trips, cold_events)
pi/blackbox/                  Daemon för Raspberry Pi (Python 3.11+)
pi/tests/                     Enhetstester för tillstånds- och trip-logik
report/generate_report.py     PDF-rapportgenerator (Supabase eller lokal SQLite)
```

## Snabbstart — utveckling utan bil

Daemonen har en mock-källa som simulerar en kallstart med en över-varvning,
uppvärmning och en kort körning (10× tidsskala). GPIO/LED degraderar till
loggning på maskiner utan GPIO.

```bash
cd pi
pip install -r requirements.txt
cp config.example.toml /tmp/blackbox.toml
# sätt: mock = true, db_path till en skrivbar sökväg, min_trip_duration_s = 10.0
python -m blackbox.main --config /tmp/blackbox.toml --verbose
```

Tester:

```bash
cd pi && python -m pytest tests/
```

## Installation på Raspberry Pi

1. Kör SQL-migrationen i Supabase (`supabase/migrations/0001_init.sql`) och skapa
   en rad i `vehicles` och en i `devices`; notera bägge UUID:erna.
2. På Pi:n, från `pi/`-katalogen: `sudo ./install.sh`
3. Redigera `/etc/blackbox.toml` (vehicle_id, device_id, ev. GPIO-pinnar) och
   `/etc/blackbox.env` (Supabase-URL + service-nyckel).
4. `sudo systemctl start blackbox && journalctl -fu blackbox`

Koppling: OBDLink SX i bilens OBD-uttag → USB på Pi:n. RGB-LED (gemensam katod)
med 330 Ω-motstånd på BCM 17 (röd), 27 (grön), 22 (blå) + GND.

| LED | Betydelse |
|---|---|
| Blå långsam blink | Väntar på motor/ECU |
| Röd fast | Motorn är kall — håll under 3 000 r/min |
| Röd snabb blink | Överträdelse pågår (loggas) |
| Grön fast | Uppvärmd |

## Ägarrapport

```bash
cd report
pip install -r requirements.txt

# Från Supabase
export SUPABASE_URL=https://YOUR-PROJECT.supabase.co
export SUPABASE_SERVICE_KEY=...
python generate_report.py --vehicle-id <uuid> --out agarrapport.pdf

# Direkt från Pi:ns databas
python generate_report.py --local-db blackbox.db \
    --vehicle-name "Porsche 911 Carrera 4S (996.2)" --vin WP0... --out agarrapport.pdf
```

`--from`/`--to` (YYYY-MM-DD) avgränsar perioden.
