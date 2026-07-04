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

Koppling: OBDLink SX i bilens OBD-uttag → USB på Pi:n. Aktiv 5 V buzzermodul
på BCM 18 + GND (signalstyrd, tre pinnar: VCC/GND/SIG).

## Signaler i bilen (buzzer)

| Ljud | Betydelse |
|---|---|
| Snabbt ihållande pipande | Överträdelse pågår — du varvar för hårt på kall motor (loggas) |
| Två korta pip | Motorn har nått full arbetstemperatur — kör på |
| Ett kort pip (valbart, av som default) | Kylvätskan varm, oljan värms fortfarande (V1.5) |

Tyst i övrigt — ingen konstant statuslampa. En RGB-LED stöds fortfarande för
den som vill (`[led] enabled = true`, BCM 17/27/22, gemensam katod, 3×330 Ω),
men är avstängd som default.

## Push till telefonen

Efter varje körning skickas en sammanfattning till din telefon via
[ntfy](https://ntfy.sh) — gratis, inget konto, ingen egen server:

1. Installera ntfy-appen (iOS/Android) och prenumerera på ett eget ämne med
   långt slumpat namn, t.ex. `blackbox-996-h7Kq2mXw9p`.
2. Sätt samma namn i `[notify] topic` i `/etc/blackbox.toml`.

Exempel: *"Körning klar ✔ — 42 min, 38.2 km (est), uppvärmd efter 23 min
(kylvätska 14, olja 23), max 4200 rpm, inga kallstartsöverträdelser"*.
Körningar med överträdelse skickas med hög prioritet.

OBS: Pi:n har bara nät i garaget (WiFi). Med tändningsstyrd ström skickas
sammanfattningen därför oftast vid **nästa** motorstart (journalåterställning) —
pushen är en sammanfattningskanal, realtidsvarningen i bilen är buzzern.

## V1.5: riktig oljetemperatur via CAN

996.2 exponerar inte oljetemp via standard-OBD, men DME:n sänder den på bilens
interna CAN-buss. Med en MCP2515-baserad CAN-HAT läser daemonen den passivt
(listen-only — Pi:n ACK:ar aldrig och är elektriskt osynlig för bilen) och
"uppvärmd" kräver då både kylvätska ≥ 80 °C och olja ≥ 80 °C.

1. Montera CAN-HAT:en och aktivera den i `/boot/firmware/config.txt` enligt
   HAT-tillverkarens anvisning (t.ex.
   `dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25` — oscillatorvärdet
   varierar mellan HAT:ar).
2. Koppla HAT:ens CAN-H/CAN-L till CAN-paret på instrumentklustrets kontakt
   (tvinnat par). Aktivera inte HAT:ens termineringsmotstånd — bussen är redan
   terminerad i bilen.
3. `install.sh` installerar `can0.service` som sätter upp interfacet i
   listen-only-läge med 500 kbit/s (justera bitrate i `can0.service` om din
   buss kör annat).
4. Verifiera dekodningen innan du litar på den: kör `candump can0 | grep 4E0`
   med varm motor och jämför `byte5 * 0.75 - 48` mot en Durametric-avläsning.
5. Sätt `enabled = true` under `[can]` i `/etc/blackbox.toml`.

Om CAN-datan uteblir (kabelbrott, HAT saknas) degraderar daemonen automatiskt
till kylvätskekriteriet och loggar vilket kriterium som gällde per körning.

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
