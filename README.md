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

## Testa på din dator först — ingen Pi behövs

Allt utom GPIO (buzzer/LED) och SocketCAN är plattformsoberoende. Tre nivåer:

1. **Utan någon hårdvara alls (5 min):** mock-läget nedan simulerar en hel
   kallstartskörning — tillståndsmaskin, körlogg, SQLite, Supabase-synk och
   push funkar på riktigt; buzzer/LED loggas i terminalen. Dashboarden och
   PDF-rapporten körs redan lokalt (demoläge resp. `--local-db`).
2. **I bilen med bara OBDLink SX + dator:** anslut adaptern, sätt
   `[obd] port` till serieporten (macOS: `/dev/tty.usbserial-XXXX`, syns med
   `ls /dev/tty.usbserial*`) och kör daemonen direkt — hela V1 (kallstartsvakt,
   automatisk körlogg, molnsynk) fungerar utan Pi. Det är också det bästa
   sättet att validera produkten innan du beställer resten av hårdvaran.
3. **CAN/oljetemp på dator:** SocketCAN finns bara på Linux. På macOS använd
   en USB-CAN-adapter (t.ex. CANable, ~300 kr) och sätt
   `[can] interface = "slcan"`, `channel = "/dev/tty.usbmodemXXXX"`,
   `bitrate = 500000` — samma inställningar finns som flaggor på
   `pi/tools/verify_oil_decode.py` (`--interface slcan --bitrate 500000`).
   Eller vänta med CAN-delen tills Pi:n är på plats.

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

## Webbdashboard — alla loggar på en sida

`web/` innehåller två statiska sidor (plus delad `ui.js`/`style.css`):

- **`index.html`** — dashboarden: nyckeltal, körfrekvens per månad, tabell
  med samtliga körningar. **Klicka på en rad** för detaljvyn: uppvärmning
  uppdelad kylvätska/olja, kriterium, och varje överträdelseepisod med
  tidpunkt, varaktighet, max-varv och temperaturer. Inloggningsskyddad
  (Supabase Auth), mörkt/ljust läge, mobilvänlig. Härifrån hanterar du
  också delningslänkarna.
- **`share.html`** — skrivskyddad "bil-CV"-sida för spekulanter och
  försäkringsbolag. Nås endast via delningslänk med token; ingen
  inloggning krävs av mottagaren och länken kan återkallas när som helst
  från dashboarden. `noindex` satt så sidan inte hamnar i sökmotorer.

Setup (engångs, ~10 min):

1. Kör `supabase/migrations/0003_dashboard_read.sql` (läsrättigheter för
   inloggade) och `0004_share_links.sql` (delningslänkar + publik
   RPC-funktion). Anon-rollen kan aldrig läsa tabellerna direkt — det enda
   som nås utan inloggning är `public_share(token)` med giltig token.
2. Skapa din användare i Supabase: Authentication → Users → Add user.
   Låt publik självregistrering vara avstängd.
3. Fyll i `SUPABASE_URL` och `SUPABASE_ANON_KEY` i konfig-blocken längst
   ner i `web/index.html` **och** `web/share.html` (anon-nyckeln är gjord
   för att vara publik — åtkomsten styrs av RLS + inloggning/token).
4. Publicera `web/`-mappen — GitHub Pages, Netlify eller Vercel (gratis).
   Utan konfig visar sidorna ett demoläge med exempeldata.

Dashboarden är stället du *tittar*; delningslänken är det du *visar upp*;
PDF:en (`report/`) är det du *skickar* som dokument.

## Push till telefonen (valbart — endast avvikelser)

Med [ntfy](https://ntfy.sh) (gratis, inget konto) kan Pi:n pusha vid
kallstartsöverträdelser — default skickas **bara** körningar med
överträdelse (`only_violations = true`); rena körningar läser du i
dashboarden i stället. Sätt `only_violations = false` om du vill ha
kvitto på varje körning.

1. Installera ntfy-appen och prenumerera på ett ämne med långt slumpat
   namn, t.ex. `blackbox-996-h7Kq2mXw9p`.
2. Sätt samma namn i `[notify] topic` i `/etc/blackbox.toml`.

Notiser köas beständigt på disk (`push_queue.json`) och skickas i ordning så
fort nät finns — de överlever strömavbrott och tappas aldrig.

## Internet via mobilens hotspot

Utan WiFi i garaget använder Pi:n telefonens delade internet. Timingen fungerar
naturligt: Pi:n är bara vaken när tändningen är på — dvs. när du och telefonen
är i bilen. Daemonen synkar och tömmer push-kön i bakgrunden **även under
pågående körning**, så förra körningens data och notiser går iväg någon minut
in i nästa körning.

Lägg in hotspoten på Pi:n (Raspberry Pi OS Bookworm, NetworkManager):

```bash
sudo nmcli connection add type wifi con-name hotspot ifname wlan0     ssid "DinIphoneHotspot" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "lösenord"     connection.autoconnect yes connection.autoconnect-priority 10
# hemma-WiFi kan ligga kvar med lägre prioritet — NetworkManager tar det som finns
```

Plattformsnoter:

- **iPhone:** hotspoten annonseras inte alltid när skärmen är låst och
  "Tillåt andra att ansluta" är av — slå på hotspoten när du sätter dig i
  bilen, eller låt "Maximera kompatibilitet" vara på. Pi:n återansluter själv
  inom ~30 s när hotspoten syns.
- **Android:** hotspot kan oftast stå på permanent (stäng av "inaktivera
  hotspot automatiskt" om telefonen har det).
- **Helt automatiskt alternativ:** USB 4G-dongel med eget data-SIM (som
  garagekameran) — noll handpåläggning, se inköpslistan.

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
4. Verifiera dekodningen innan du litar på den — ingen Durametric behövs:

   ```bash
   # starta med KALL motor, kör/tomgångskör ~20 min
   python3 pi/tools/verify_oil_decode.py --port /dev/ttyUSB0 --out /tmp/oilverify
   ```

   Verktyget läser OBD-kylvätsketemp (referens) och CAN-kandidaten samtidigt
   och kontrollerar fysiken varje M96-uppvärmning måste följa: samma
   starttemperatur som kylvätskan, monoton stigning, når 80 °C flera minuter
   EFTER kylvätskan, rimlig platå (75–115 °C) och hög korrelation. Alla
   PASS ⇒ dekodningen stämmer. Något FAIL ⇒ kör om med `--dump`, som även
   spelar in hela rå-CAN-tracen + OBD-referensen som tidssynkade CSV:er —
   exakt det format korrelationsbaserad reverse engineering behöver (t.ex.
   CSS Electronics öppna Claude Code-skill för CAN-reverse-engineering).
   En Durametric-avläsning fungerar förstås också som facit.
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
