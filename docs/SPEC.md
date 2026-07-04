# Black Box V1 — Teknisk specifikation

Produkt: en OBD-II-ansluten "svart låda" för entusiastbilar (referensbil: Porsche 996.2 C4S, M96)
som skyddar motorn vid kallstart, loggar varje körning automatiskt och genererar en
trovärdig ägarrapport i PDF.

Detta dokument är granskningsunderlaget: arkitektur, datamodell, tillståndslogik och de
avvägningar som kräver beslut. Koden i `pi/`, `supabase/` och `report/` implementerar exakt
det som står här.

---

## 1. Systemarkitektur

```
   ┌────────────────────────────── Bilen ──────────────────────────────┐
   │                                                                   │
   │  OBD-II-port ──► OBDLink SX (USB, ELM327-kompatibel)              │
   │                        │                                          │
   │  CAN-buss (kluster) ──► MCP2515 CAN-HAT, listen-only (V1.5)       │
   │                        │        │                                 │
   │                        ▼        ▼                                 │
   │  ┌──────────────── Raspberry Pi (Zero 2 W / 4) ────────────────┐  │
   │  │  blackbox-daemon (Python, systemd)                          │  │
   │  │   • Poller: RPM (010C), kylvätska (0105), hastighet (010D)  │  │
   │  │   • CAN-lyssnare: oljetemp (ID 0x4E0, V1.5)                 │  │
   │  │   • Tillståndsmaskin: KALL → KYLV. VARM → UPPVÄRMD (latch)  │  │
   │  │   • Överträdelselogik: RPM > 3000 före UPPVÄRMD ⇒ händelse  │  │
   │  │   • Trip-detektering: motor på/av ⇒ körning start/slut      │  │
   │  │   • Buzzer via GPIO (varningspip/klar-signal); LED valbar   │  │
   │  │   • SQLite-buffert (offline-first, källa för sanning lokalt)│  │
   │  └───────────────┬─────────────────────────────────────────────┘  │
   └──────────────────┼────────────────────────────────────────────────┘
                      │  WiFi (garage) — synk när nät finns
                      ▼
        Supabase (Postgres + PostgREST)
         • vehicles, devices, trips, cold_events
                      │
                      ▼
        report/generate_report.py  ──►  Ägarrapport.pdf
        (körs på Mac/server, läser Supabase eller Pi:ns SQLite direkt)
```

### Designprinciper

1. **Offline-first.** Bilen har inte nät. Allt skrivs först till SQLite på Pi:n;
   synk till Supabase sker opportunistiskt (direkt efter avslutad körning om nät finns,
   annars nästa gång Pi:n har uppkoppling, t.ex. hemma i garaget). Ingen data går
   förlorad av att molnet är onåbart.
2. **All säkerhetskritisk logik körs lokalt.** Ljudsignalen och kallstartslogiken är
   oberoende av nätverk och moln — de fungerar identiskt i ett garage utan täckning.
   Telefonpushen är en sammanfattningskanal, aldrig realtidsvarning (bilen saknar nät
   under körning).
3. **Molnet är för lagring och rapporter**, inte för realtid. Supabase är systemets
   arkiv; Pi:n är sensorn.
4. **Klientgenererade UUID:er** för trips/events gör synken idempotent (upsert på id) —
   omkörning av synk kan aldrig skapa dubbletter.

### Hårdvara (antaganden, se beslutslista)

| Komponent | Val (antaget) | Motivering |
|---|---|---|
| Dator | Raspberry Pi Zero 2 W | Billigast/minst som klarar jobbet; Pi 4 fungerar identiskt för utveckling |
| OBD-adapter | **OBDLink SX (USB)** | Kabelbunden = inga Bluetooth-parningsproblem, ingen av ELM327-klonernas flakiness, snabb och väldokumenterad med `python-OBD`. BT-kloner (~100 kr) fungerar men är produktens största felkälla. |
| Buzzer | Aktiv 5 V buzzermodul (3-pin) på GPIO 18 | Primär förarindikator: händelseljud i stället för statuslampa — inget ingrepp i inredningen |
| LED (valbar) | Gemensam katod RGB-LED + 3×330 Ω på GPIO 17/27/22 | Alternativ/komplement för den som vill ha visuell status (default av) |
| CAN (V1.5) | MCP2515 SPI CAN-HAT (~150 kr) | Riktig oljetemp, lyssnad passivt från CAN-paret vid instrumentklustret (listen-only) |
| Ström | USB-adapter i 12V-uttag **eller** buck-omvandlare från OBD stift 16 | Se beslut B1 — 996:ans OBD-port har konstant 12 V, batteridränering måste hanteras |

**Viktigt om ström (996):** OBD-stift 16 är strömsatt även med avslagen tändning. En Pi
Zero 2 W drar ~0,6–1 W i idle ⇒ ~15–25 Ah/månad, vilket dränerar ett stillastående
entusiastbilsbatteri. V1-rekommendation: mata Pi:n från tändningsstyrd källa (12V-uttag
om tändningsstyrt, annars säkringsuttag med add-a-fuse på tändningsstyrd krets) så att
enheten bara lever när motorn kan vara igång. Daemonen är byggd för hård strömavstängning:
SQLite skrivs transaktionellt per körning och en körning som kapas av strömavbrott sparas
med sista kända värden vid nästa uppstart (journalfil).

---

## 2. Datamodell (Supabase/Postgres)

Fyra tabeller. Fullständig DDL i `supabase/migrations/0001_init.sql`.

### `vehicles`
| Fält | Typ | Beskrivning |
|---|---|---|
| `id` | `uuid` PK | |
| `display_name` | `text` NOT NULL | t.ex. "Porsche 996.2 C4S" |
| `vin` | `text` UNIQUE | Chassinummer — bär trovärdigheten i rapporten |
| `make` / `model` | `text` | |
| `model_year` | `int` | |
| `created_at` | `timestamptz` | |

### `devices`
| Fält | Typ | Beskrivning |
|---|---|---|
| `id` | `uuid` PK | Genereras vid provisionering, ligger i Pi:ns config |
| `vehicle_id` | `uuid` FK → vehicles | |
| `name` | `text` | t.ex. "Pi Zero garage" |
| `last_seen_at` | `timestamptz` | Uppdateras vid varje lyckad synk (hälsokoll) |

### `trips` — en rad per körning
| Fält | Typ | Beskrivning |
|---|---|---|
| `id` | `uuid` PK | Klientgenererad (idempotent synk) |
| `vehicle_id` | `uuid` FK NOT NULL | |
| `device_id` | `uuid` FK | |
| `started_at` / `ended_at` | `timestamptz` | Motor på / motor av |
| `duration_s` | `int` | |
| `distance_km_est` | `numeric(7,2)` | Integrerad ur PID 010D (hastighet), märkt "est." i rapporten |
| `warmed_up` | `boolean` | Nådde motorn UPPVÄRMD (enligt gällande kriterium)? |
| `warmup_s` | `int` NULL | Sekunder från start till helt UPPVÄRMD (NULL om aldrig varm) |
| `coolant_warmup_s` | `int` NULL | Sekunder till kylvätsketröskeln |
| `oil_warmup_s` | `int` NULL | Sekunder till oljetröskeln (NULL utan oljedata) |
| `warm_criterion` | `text` | `coolant` \| `oil_and_coolant` — kriteriet som faktiskt gällde |
| `max_rpm` | `int` | |
| `max_coolant_c` | `numeric(5,1)` | |
| `max_oil_temp_c` | `numeric(5,1)` NULL | Från CAN-lyssnaren (V1.5) |
| `cold_violation_count` | `int` NOT NULL DEFAULT 0 | Antal överträdelseepisoder |
| `source` | `text` DEFAULT 'pi' | Framtidssäkring (manuell import etc.) |
| `created_at` | `timestamptz` | |

### `cold_events` — en rad per överträdelseepisod
| Fält | Typ | Beskrivning |
|---|---|---|
| `id` | `uuid` PK | Klientgenererad |
| `trip_id` | `uuid` FK → trips ON DELETE CASCADE | |
| `started_at` / `ended_at` | `timestamptz` | Episodens varaktighet |
| `duration_s` | `numeric(6,1)` | |
| `max_rpm` | `int` NOT NULL | Högsta varvtal under episoden |
| `coolant_c_at_start` | `numeric(5,1)` | Kylvätsketemp när episoden började |
| `oil_c_at_start` | `numeric(5,1)` NULL | Oljetemp när episoden började (V1.5) |

**Medvetet utelämnat i V1:** rådatasamples (1–2 Hz tidsserie). Rapporten behöver bara
aggregat per körning, och samples ökar lagring/synkvolym ~1000×. Pi:n behåller dock
senaste körningens samples i en lokal journalfil för felsökning. Kan bli `trip_samples`
(nedsamplad till 0,1 Hz) i V1.5 om graf-per-körning efterfrågas.

**RLS:** aktiverat på alla tabeller. V1 (egen enhet) använder service-nyckel från Pi:n,
som går förbi RLS. För produkt-skala: byt till per-enhet JWT via Edge Function (beslut B4).

---

## 3. Tillståndslogik

### 3.1 Uppvärmningstillstånd (per körning)

```
        coolant ≥ warm_coolant_c            olja ≥ warm_oil_c (default 80 °C)
KALL ─────────────────────────► KYLV. VARM ─────────────────────► UPPVÄRMD
  │      (default 80 °C)             │                                │
  └─ LED: röd (fast)                 └─ LED: gul (fast)               └─ LED: grön (fast)

Utan oljedata (V1, eller CAN-avbrott): KALL ──► UPPVÄRMD direkt på kylvätskan.
```

Alla latchar är permanenta inom körningen: termostaten kan få temperaturen att dippa
under 80 °C i fartvind, men motorn är fortfarande uppvärmd. Nytt trip ⇒ ny
tillståndsmaskin.

**Oljetemperatur (V1.5).** M96:ans olja släpar 10+ minuter efter kylvätskan, och en
grön lampa på enbart kylvätska vore missvisande för målgruppen. 996.2 exponerar inte
oljetemp via standard-OBD, men DME:n sänder den på interna CAN-bussen
(DME ↔ instrumentkluster). Dekodning (community-dokumenterad, **verifieras mot
Durametric före skarp användning**): CAN-ID `0x4E0`, byte 5, 8 bitar, `fys = råvärde
× 0,75 − 48`. Hårdvara: MCP2515 SPI CAN-HAT inkopplad på CAN-paret vid
instrumentklustret, med interfacet i **listen-only-läge** (ACK:ar aldrig, sänder
aldrig error frames — elektriskt osynlig för bilen; `pi/can0.service` sätter upp
detta). Kriteriet styrs av `warm_criterion`: `auto` (olja krävs när CAN är aktiv),
`coolant`, eller `oil_and_coolant`. **Degradering:** om oljedata krävs men aldrig
setts under körningen (kabelbrott, HAT saknas) faller maskinen tillbaka till
kylvätskekriteriet så att lampan fortfarande kan bli grön; vilket kriterium som
faktiskt gällde sparas per körning i `trips.warm_criterion`.

### 3.2 Överträdelseepisoder

En **episod** (inte ett sample) loggas så här, för att en sekunds rusning inte ska bli
20 rader vid 2 Hz-polling:

- **Start:** `rpm > cold_rpm_limit` (default 3000) innan tillstånd = UPPVÄRMD
  (dvs. även i KYLV. VARM-fasen — oljan är fortfarande kall).
  LED byter till snabbt blinkande rött.
- **Under:** `max_rpm` uppdateras löpande.
- **Slut:** `rpm < cold_rpm_limit − rpm_hysteresis` (default 300) sammanhängande i
  `violation_end_delay_s` (default 2,0 s), **eller** motorn blir helt UPPVÄRMD,
  **eller** körningen tar slut.
- Episoden sparas med starttid, sluttid, varaktighet, max-RPM och kylvätsketemp vid start.

### 3.3 Trip-detektering (helt automatisk)

| Övergång | Villkor |
|---|---|
| **Trip startar** | RPM ≥ 300 (motorn snurrar — skiljer körning från tändning-på) |
| **Trip slutar** | RPM < 100 (eller null) sammanhängande i `engine_off_end_s` (60 s), **eller** OBD-förbindelsen tappas i `disconnect_end_s` (45 s) — på de flesta bilar slutar ECU:n svara när tändningen slås av |
| **Kastas** | Körningar kortare än `min_trip_duration_s` (60 s) sparas inte (nyckelvridningar, flytt i garaget) — konfigurerbart |

Sträcka uppskattas som trapetsintegral av PID 010D (km/h) över sampeltid, med dt
kapat till 5 s så att sampel-luckor inte skapar fantomkilometer. Rapporten märker
alltid sträckan "uppskattad via OBD".

### 3.4 Förarsignaler

Primär indikator är **ljud** (aktiv buzzer, GPIO 18) — händelsestyrd i stället
för konstant lampa: hörs utan att blicken flyttas och kräver inget synligt
ingrepp i inredningen.

| Ljud | Händelse |
|---|---|
| Snabbt ihållande pipande | Överträdelseepisod pågår (slutar när varvet släpps) |
| Två korta pip | Motorn helt UPPVÄRMD |
| Ett kort pip (valbart, default av) | KYLVÄTSKA VARM-fasen nådd (V1.5) |

**Push till telefon** (ntfy, valbar): sammanfattning per körning (varaktighet,
sträcka, uppvärmningstider, max-rpm, överträdelser) med hög prioritet vid
överträdelse. Skickas när nät finns — med tändningsstyrd ström i praktiken vid
nästa motorstart via journalåterställningen; realtid i bilen är buzzerns jobb.

**RGB-LED** stöds fortfarande (`[led]`, default av): blå blink = väntar,
röd = KALL, röd snabb blink = överträdelse, gul = KYLV. VARM, grön = UPPVÄRMD.

---

## 4. Synk och rapport

- **Synk:** efter varje avslutad körning, samt periodiskt (`sync_interval_s`, 300 s) när
  daemonen väntar på motorn. Upsert (`Prefer: resolution=merge-duplicates`,
  `on_conflict=id`) av trips först, sedan cold_events (FK-ordning). Rader markeras
  `synced` i SQLite först efter 2xx-svar. `devices.last_seen_at` uppdateras som hälsopuls.
- **Rapport:** `report/generate_report.py` läser Supabase (eller Pi:ns SQLite direkt med
  `--local-db`), aggregerar och renderar PDF med ReportLab: sidhuvud med bil + VIN +
  period, nyckeltalsrad (antal körningar, sträcka, körtid, andel körningar utan
  kall-överträdelse, senast körd, medianuppvärmningstid), stapeldiagram över körningar
  per månad (12 mån), tabell över senaste körningar, samt fotnot om metodik.
  Typografi och färger är valda för att se ut som ett intyg, inte en hobbylogg.

---

## 5. Beslut som behöver fattas (B1–B6)

| # | Beslut | Alternativ | Rekommendation |
|---|---|---|---|
| **B1** | Strömförsörjning | (a) Tändningsstyrd matning (add-a-fuse/12V-uttag) — enheten är död när bilen står. (b) Konstant ström från OBD-stift 16 + undervoltsvakt — möjliggör framtida "hjärtslag när bilen står" men riskerar batteriet. | **(a)** för V1. Enkelt, säkert, och V1 har inget att göra när motorn är av. |
| **B2** | OBD-adapter | OBDLink SX USB (~600 kr, robust) vs ELM327 BT-klon (~100 kr, flakig) | **OBDLink SX**. För en betalprodukt är adapterflakiness den dyraste supportkostnaden. |
| **B3** | Varm-tröskel | Kylvätska 80 °C (V1) vs kylvätska + olja 80/80 °C via CAN (V1.5). | **Implementerat: bägge.** Med CAN-HAT gäller kylvätska + olja (`warm_criterion = "auto"`); utan oljedata degraderar systemet automatiskt till kylvätska. Kvarstående delbeslut: verifiera CAN-dekodningen mot Durametric och bekräfta bitrate 500 kbit/s på din bil. |
| **B4** | Moln-autentisering | Service-nyckel på Pi:n (V1, egen enhet) vs per-enhet JWT via Edge Function (produkt) | **Service-nyckel nu**, JWT före första externa kund. Nyckeln ligger i `/etc/blackbox.env` (root-läsbar), inte i repo. |
| **B5** | PDF-generering | Lokalt Python/ReportLab-skript (V1) vs server-side (Edge Function + Storage, del av månadsavgiften) | **Lokalt skript nu** — noll hosting. Flytta till moln när månadsavgiften ska motiveras. |
| **B6** | Korta körningar | Kasta < 60 s (default) eller logga allt | **Kasta**, konfigurerbart — garagerangering i rapporten sänker trovärdigheten. |

## 6. Utanför scope (bekräftat)

GPS/IMU, ljud/vibration, väder, coaching och körpåminnelser byggs inte. Datamodellen
blockerar inget av dem: påminnelser (V1.5) är en ren fråga mot `trips.started_at`.
