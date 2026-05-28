# Libratone Lounge — DIY Active DSP Soundbar

## Projektübersicht

Umbau eines gebrauchten Libratone Lounge zu einer aktiven 5-Kanal DSP-Soundbar für TV-Betrieb. Sealed Enclosure, kein passives Crossover — alle 5 Kanäle unabhängig über CamillaDSP gesteuert. True Stereo über gegenüberliegende Treiberpaare.

## Treiber

| Weg | Treiber | Größe | Anzahl | DCR | Nennimpedanz (geschätzt) | Position |
|-----|---------|-------|--------|-----|--------------------------|----------|
| Bass | OEM Woofer | 8" | 1× | 3,2Ω | ~4Ω | Mitte |
| Mittelton | OEM Mid | 4" | 2× | 2,7Ω | ~4Ω | L/R symmetrisch |
| Hochton | OEM Ribbon-Tweeter | — | 2× | 4,1Ω | ~6Ω | L/R symmetrisch |

- Sealed Enclosure — kein Bassreflex, kein passives Crossover
- True Stereo: jeweils 1× Mid + 1× Ribbon pro Seite
- 5 unabhängige Amp-Kanäle für volle DSP-Kontrolle

## Hardware-Plattform

### Computer
- **Raspberry Pi 5 (8 GB)** — geplant (Vorgängerplan war Pi 4; aufgegeben
  zugunsten Pi 5 wegen mehr Headroom für CDSP + Web-UI + RP1-I/O-Subsystem
  das I²S/PWM-Konflikte entzerrt)
- OS: Raspberry Pi OS Lite (64-bit), SD mit Overlay Filesystem

### Verstärker (Sonocotta, Tindie)

- **1× Louder Hat Plus 2X** (2× TAS5825M, 4 Kanäle)
- **1× Louder Hat 1X non-Plus** (1× TAS5825M, 2 Kanäle → 1 Kanal genutzt)
- Gesamt: 5 unabhängige Amp-Kanäle

**Stacking-Regel:** Zwei Plus-Boards lassen sich **nicht** stacken — identische I²C-Adressen (0x4C/0x4D). Plus + non-Plus funktioniert, weil der non-Plus im Adressbereich 0x2D/0x2E liegt (Andriy/Discord bestätigt).

### Hardware-Stack — Aufbaureihenfolge (von unten nach oben)

| Ebene | Board | Begründung |
|-------|-------|------------|
| 1 (unten) | Raspberry Pi 5 | Basis |
| 2 | Louder Hat Plus 2X | DC-Eingang (24 V Barrel Jack) + Step-Down → 5 V an Pi über 40-Pin-Header. Muss direkt auf dem Pi sitzen für saubere Stromversorgung. |
| 3 | Louder Hat 1X (non-Plus) | Zieht 5 V + 24 V über durchgeschleiften Header vom Plus-Board darunter. |
| 4 (oben) | Lochrasterplatine | Verdrahtung UI-Board (Taste + LEDs) auf GPIO-Header-Pins. Keine aktiven Bauteile nötig — reine Kabelbrücke. |

### UI — Taste + LED-Ring (Original Libratone)

Originalbestückung des Libratone Lounge: eine Taste und ein LED-Ring mit drei Farben (je 3× SMD-LEDs). Transistoren (Q101–Q103) und Vorwiderstände sitzen bereits auf dem Original-UI-Board — keine zusätzlichen Bauteile auf der Lochrasterplatine nötig.

#### UI-Board Pinbelegung (7-Pin-Stecker)

| UI-Pin | Funktion | Versorgung |
|--------|----------|------------|
| 1 | Taster (schließt gegen Pin 6) | |
| 2 | Rot ein (Q103 Basis) | |
| 3 | Gelb ein (Q102 Basis) | |
| 4 | Weiß ein (Q101 Basis) | |
| 5 | GND (Emitter) | |
| 6 | VCC Rot + Gelb | 3,3 V |
| 7 | VCC Weiß | 5 V |

#### GPIO-Zuordnung (UI-Board → Raspberry Pi)

| UI-Pin | Funktion | RPi GPIO | Header-Pin (phys.) | Hinweis |
|--------|----------|----------|--------------------|---------|
| 1 | Taster | GPIO 17 | Pin 11 | Active-High: Taster schließt gegen 3,3 V (Pin 6). GPIO mit internem Pull-Down konfigurieren, steigende Flanke auswerten. |
| 2 | Rot (Q103 Basis) | GPIO 22 | Pin 15 | Low-Side-Transistor auf UI-Board, GPIO treibt nur Basis (~mA). |
| 3 | Gelb (Q102 Basis) | GPIO 23 | Pin 16 | Wie Rot. PWM-dimmbar über `pigpio` Software-PWM. |
| 4 | Weiß (Q101 Basis) | GPIO 24 | Pin 18 | Wie Rot. PWM-dimmbar über `pigpio` Software-PWM. |
| 5 | GND | GND | Pin 14 | |
| 6 | VCC 3,3 V | 3V3 | Pin 17 | Versorgt rote + gelbe LEDs über UI-Board |
| 7 | VCC 5 V | 5V | Pin 4 | Versorgt weiße LEDs über UI-Board |

Die physischen Header-Pins 4, 11, 14–18 liegen nah beieinander → kurze Lötbrücken auf der Lochrasterplatine.

#### PWM-Dimming

Hardware-PWM auf dem Pi 5 läuft über das RP1-Southbridge-IC und nicht mehr
über den SoC direkt — die Pi-4-Faustregel "GPIO 12/13 kollidieren mit
I²S-Clock-Generator" gilt nicht mehr 1:1, weil RP1 das I/O-Subsystem
entkoppelt. Trotzdem ist `pigpio`-Software-PWM (DMA-basiert) hier weiterhin
der pragmatische Weg: portabel zwischen Pi 4/5, jeder GPIO nutzbar, 200–
500 Hz für LED-Dimming locker drin, CPU-Last vernachlässigbar. Hardware-
PWM nur dann verwenden wenn ein Software-Pfad sich als zu unruhig erweist
(unwahrscheinlich bei LED-Frequenzen).

### GPIO-Gesamtbelegung — Raspberry Pi 5

| GPIO | Phys. Pin | Funktion | Belegt durch |
|------|-----------|----------|--------------|
| 2 | 3 | I²C SDA | DAC-Stack (alle 3 DACs) |
| 3 | 5 | I²C SCL | DAC-Stack (alle 3 DACs) |
| 4 | 7 | PDN DAC #1 | Louder Hat Plus 2X (Primary, 0x4C) |
| 5 | 29 | PDN DAC #2 | Louder Hat Plus 2X (Secondary, 0x4D) |
| 6 | 31 | PDN DAC #3 | Louder Hat 1X non-Plus (0x2D) — im Custom-Overlay als `pdn_gpio_tertiary` definieren |
| 17 | 11 | Taster (Input) | UI-Board Pin 1 |
| 18 | 12 | I²S BCK | DAC-Stack |
| 19 | 35 | I²S LRCK | DAC-Stack |
| 20 | 38 | I²S DIN | DAC-Stack |
| 21 | 40 | I²S DOUT | DAC-Stack |
| 22 | 15 | LED Rot (Output) | UI-Board Pin 2 |
| 23 | 16 | LED Gelb (Output, PWM) | UI-Board Pin 3 |
| 24 | 18 | LED Weiß (Output, PWM) | UI-Board Pin 4 |

**Frei verfügbar:** GPIO 7–13, 14–16, 25–27 — Reserve für spätere Erweiterungen (z. B. IR-Empfänger auf GPIO 17 wäre Andreys Default, hier aber für Taster belegt → ggf. GPIO 25 für IR).

### Netzteil
- **24 V / 5 A (~120 W)** — MeanWell oder vergleichbar
- Barrel Jack 5,5 × 2,1mm
- Soft-Start (MOSFET IRLZ44N + RC ~100 ms) geplant wegen Einschaltstrom großer Kapazitäten

### Kanalzuordnung (vorläufig)

| Amp-Board | TAS Adresse | Kanal | Treiber |
|-----------|-------------|-------|---------|
| Plus 2X Primary | 0x4C | L | Mid L (4") |
| Plus 2X Primary | 0x4C | R | Mid R (4") |
| Plus 2X Secondary | 0x4D | L | Ribbon L |
| Plus 2X Secondary | 0x4D | R | Ribbon R |
| 1X non-Plus | 0x2D | L (PBTL) | Woofer 8" |

**Hinweis:** Kanalzuordnung TBD — hängt vom Custom Device Tree Overlay ab.

## Software-Stack

| Komponente | Software |
|------------|----------|
| Audio DSP | CamillaDSP 4.x (5-Wege aktiv Crossover, PatchConfig) |
| Spotify Connect | go-librespot (HTTP API, localhost:3678) |
| TV Audio | TOSLINK via USB S/PDIF Adapter |
| Smart Home | MQTT → Home Assistant (Auto-Discovery) |
| Multi-Room | Snapcast |
| UI-Steuerung | Python GPIO (`pigpio` für PWM + Taster) |

## Signalkette

```
Quellen (Spotify / TOSLINK / Snapcast / BT)
    → ALSA Loopback hw:Loopback,0
    → CamillaDSP (5-Wege Crossover + EQ + Volume)
        → hw:LouderRaspberry,0 (6-ch, S32_LE/48kHz)
        → Kanal 0 → Mid L
        → Kanal 1 → Mid R
        → Kanal 2 → Ribbon L
        → Kanal 3 → Ribbon R
        → Kanal 4 → Woofer (PBTL)
```

**Hinweis:** ALSA-Device und Kanalzahl hängen vom Custom DT Overlay ab. Das aktuelle Sonocotta-Treiberpaket unterstützt nur 2 DACs. 3 DACs erfordern ein Custom Overlay.

## Offene Punkte

### Hardware
- [x] Bestellung bei Tindie zusammenstellen (1× Louder Hat Plus 2X + 1× Louder Hat 1X non-Plus)
- [x] 24 V / 5 A Netzteil + DC-Buchse
- [x] USB-S/PDIF-Adapter für TOSLINK-Eingang aussuchen
- [ ] Soft-Start-MOSFET vorbereiten
- [ ] Kühlkonzept (Lüfter / Belüftungsschlitze im Gehäuse)
- [x] Original-UI reverse-engineered: 7-Pin-Stecker, Transistoren Q101–Q103 on-board, Pinbelegung dokumentiert
- [x] Lochrasterplatine bestücken: 7 Lötbrücken UI-Board → Pi-Header (keine aktiven Bauteile)
- [ ] UI-Board Funktion testen (LEDs + Taster) vor Einbau ins Gehäuse

### Software / Konfiguration
- [ ] Custom DT Overlay für 3-DAC-Stack (Sonocotta/Andrey)
- [ ] CamillaDSP 5-Wege-Konfiguration erstellen
- [ ] REW-Messung pro Treiber (Nahfeld + Fernfeld)
- [ ] Crossover-Frequenzen bestimmen (Mid→Ribbon, Woofer→Mid)
- [ ] TOSLINK-Quelle über USB S/PDIF integrieren
- [ ] `pigpio`-Service für LED-Steuerung + Taster-Handler einrichten
- [ ] PWM-Dimming für Gelb + Weiß (z. B. Standby = Gelb gedimmt, Active = Weiß voll)
- [ ] Taster-Logik definieren (Kurzdrück = Play/Pause, Langdrück = Standby/Wakeup o. ä.)
- [ ] Snapcast aktivieren + testen

## Anmerkungen

- Custom DT Overlay ist der kritische Blocker — ohne das geht die 5-Kanal-Konfiguration nicht
- Das Original-UI-Board ist elegant: Transistoren + Vorwiderstände on-board, Pi muss nur GPIOs schalten
- Hardware-PWM auf dem Pi 5 ist via RP1-Southbridge anders gelagert als beim Pi 4 (kein I²S-Konflikt mehr), aber `pigpio` Software-PWM bleibt der portable + risikoarme Weg für LED-Dimming
- Einschaltstrom der MeanWell + HAT-Kapazitäten erfordert MOSFET Soft-Start (Lesson learned von Beat #1)
