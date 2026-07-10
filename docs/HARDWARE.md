# Black Box — Inköpslista hårdvara

Cirkapriser juli 2026, SEK inkl. moms. Kolla dagspris — särskilt Pi-priser rör sig.
Kopplingar och pinnar refererar till `docs/SPEC.md` och `pi/config.example.toml`.

## A. Kärna (V1)

| # | Komponent | Spec / exempel | Ca-pris | Var | Notering |
|---|---|---|---|---|---|
| 1 | Raspberry Pi Zero 2 W **med header** | alt. Pi 4 Model B 2 GB för utveckling | 300 / 700 | Electrokit, Kjell | Zero 2 W räcker gott i drift. Pi 4 är bekvämare att utveckla på (full USB-A, mer ork) — har du en liggande: börja där. CAN-HAT:en kräver 40-pins header, så välj Zero-varianten med förlödd header. |
| 2 | microSD 32 GB **High Endurance** | SanDisk High Endurance / Samsung PRO Endurance | 150 | Kjell, Webhallen | Viktigt: enheten strömdödas vid varje körningsslut (tändningsstyrd matning). Endurance-kort tål det; billigaste korten gör det inte. |
| 3 | OBD-adapter: **OBDLink SX (USB)** | | 700–900 | Amazon.se, Carvitas (officiell ÅF) | Produktens viktigaste kvalitetsval — undvik ELM327-BT-kloner. Kabelbunden = inga parningsproblem. |
| 4 | USB OTG-adapter, micro-USB → USB-A hona | | 50 | Kjell | Endast för Pi Zero (dess enda dataport är micro-USB). Behövs ej med Pi 4. |

**Delsumma A: ca 1 200–1 800 kr**

## B. Strömförsörjning (tändningsstyrd — beslut B1a)

| # | Komponent | Spec / exempel | Ca-pris | Var | Notering |
|---|---|---|---|---|---|
| 5 | Add-a-fuse / säkringsadapter | ATO-format (996:ans centralelektronik) + 5 A säkring | 60 | Biltema, Jula | Sätts på en **tändningsstyrd** krets i säkringspanelen (förarfotbrunnen). Verifiera med multimeter att kretsen dör med tändningen. |
| 6 | DC-DC-omvandlare 12 V → 5 V, ≥3 A | Gärna 9–36 V ingång med transientskydd, USB- eller skruvutgång | 100–150 | Biltema, Amazon.se, Electrokit | Bilelnät spikar vid start — snåla inte här. |
| 7 | Kablage + kontaktdon | 1,5 mm² kabel, flatstiftshylsor, ringkabelsko (jord), krympslang | 80 | Biltema | Jorda mot karossskruv nära monteringen. |
| — | *Enklare alternativ:* USB-laddare i 12 V-uttaget | | 100 | | **Endast om uttaget är tändningsstyrt** — på 996 är cigguttaget normalt konstantmatat: mät innan du väljer denna väg, annars dräneras batteriet. |
| — | *Valbart (uppkoppling):* USB 4G-dongel + data-SIM | Huawei E3372 el. motsv. + IoT/kontantkorts-SIM | 300–500 + ~20/mån | Kjell, Amazon.se; SIM: Comviq/Vimla/Telia | Utan WiFi i garaget är grundplanen mobilens hotspot (gratis, se README). Dongeln gör synk/push helt automatisk — samma modell som garagekameran. |

**Delsumma B: ca 250–350 kr**

## C. Förarindikator (ljud)

| # | Komponent | Spec / exempel | Ca-pris | Var | Notering |
|---|---|---|---|---|---|
| 8 | **Aktiv buzzermodul 5 V**, 3-pin (VCC/GND/SIG) | t.ex. KY-012 eller motsv. modul | 25 | Electrokit, Kjell | "Aktiv" = inbyggd oscillator, styrs hög/låg från GPIO 18. Köp inte passiv piezo (kräver PWM-drivning och låter svagare). |
| 9 | Dupont-kablar hona–hona | | 20 | Electrokit | Buzzern kan sitta kvar inne i lådan — ljud tar sig igenom panelen. |
| — | *Valbart:* RGB-LED 5 mm gemensam katod + 3×330 Ω + panelhållare | | 60 | Electrokit | Endast om du vill ha visuell status som komplement (`[led] enabled = true`). |

**Delsumma C: ca 45 kr (105 kr med valbar LED)**

> Telefonpushen (ntfy) kräver ingen hårdvara alls — bara appen på telefonen.

## D. CAN-tillägget för riktig oljetemp (V1.5)

| # | Komponent | Spec / exempel | Ca-pris | Var | Notering |
|---|---|---|---|---|---|
| 11 | **Waveshare RS485 CAN HAT** (MCP2515) | | 150–250 | Electrokit | Monteras på 40-pinsheadern. **Aktivera inte termineringsbygeln** — bilens buss är redan terminerad. Oscillatorvärdet (8/12/16 MHz står på kristallen) ska matcha `dtoverlay`-raden, se README. |
| 12 | Tvinnat kabelpar 0,35–0,5 mm², ~2 m | | 30 | Biltema, Electrokit | CAN-H/CAN-L från instrumentklustrets kontakt till HAT:en. Behåll tvinningen hela vägen. |
| 13 | Skarvdon: Posi-Tap/Scotchlok, alt. lödning + krympslang | | 60 | Biltema, Amazon.se | Posi-Tap är återställningsbart utan att klippa kabel — snällast mot en entusiastbil. |
| 14 | Verifiering av dekodningen | `pi/tools/verify_oil_decode.py` | 0 | ingår | Självverifiering mot OBD-referensen under en uppvärmning — ingen Durametric behövs. Vid FAIL: `--dump` spelar in rå trace + referens för korrelationsbaserad reverse engineering. |

**Delsumma D: ca 250–350 kr**

## E. Montering & övrigt

| # | Komponent | Spec / exempel | Ca-pris | Var | Notering |
|---|---|---|---|---|---|
| 15 | Kapsling ABS, ventilerad | rymmer Pi + HAT + buck | 80–150 | Electrokit, Kjell | Placering: under/bakom klädsel nära säkringspanelen. Undvik direkt solljus (kupétemp sommartid). |
| 16 | Kardborre, buntband, reservsäkringar 5 A | | 50 | Biltema | |
| 17 | Multimeter (om du saknar) | | 150 | Biltema | Krävs för att hitta tändningsstyrd krets och verifiera 12 V-uttaget. |

**Delsumma E: ca 130–350 kr**

---

## Totalsummor

| Konfiguration | Ca-pris |
|---|---|
| **V1 komplett** (A + B + C + E) | **1 650–2 550 kr** |
| **V1.5-tillägget** (D) | **+250–350 kr** |

## Beställningsordning (praktiskt)

1. **Beställ nu:** OBDLink SX (längst leveranstid om Carvitas/Amazon inte har lager) + Pi + SD-kort — räcker för att köra V1 på bänken med mock och sedan i bilen via 12 V-uttag provisoriskt.
2. **Biltema-rundan:** allt i B + E när du vet var enheten ska sitta.
3. **CAN-delarna (D):** beställ med Pi-ordern från Electrokit, men koppla in först när V1 rullat en vecka felfritt — då vet du att basen är stabil innan du felsöker CAN.
