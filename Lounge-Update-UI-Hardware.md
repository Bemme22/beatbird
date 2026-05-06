# Lounge.md — Update: Hardware-Stack & UI-Anbindung

> Neue/geänderte Sektionen zum Einpflegen in `Lounge.md`.
> Markiert mit `[NEU]` oder `[GEÄNDERT]`.

---

## [NEU] Hardware-Stack — Aufbaureihenfolge

Einfügen unter **Hardware-Plattform → Verstärker**, nach dem Abschnitt über die Stacking-Regel.

```markdown
### Hardware-Stack — Aufbaureihenfolge (von unten nach oben)

| Ebene | Board | Begründung |
|-------|-------|------------|
| 1 (unten) | Raspberry Pi 4 | Basis |
| 2 | Louder Hat Plus 2X | DC-Eingang (24 V Barrel Jack) + Step-Down → 5 V an Pi über 40-Pin-Header. Muss direkt auf dem Pi sitzen für saubere Stromversorgung. |
| 3 | Louder Hat 1X (non-Plus) | Zieht 5 V + 24 V über durchgeschleiften Header vom Plus-Board darunter. |
| 4 (oben) | Lochrasterplatine | Verdrahtung UI-Board (Taste + LEDs) auf GPIO-Header-Pins. Keine aktiven Bauteile nötig — reine Kabelbrücke. |

**Wichtig:** Zwei Plus-Boards lassen sich **nicht** stacken — identische I²C-Adressen (0x4C/0x4D). Plus + non-Plus funktioniert, weil der non-Plus im Adressbereich 0x2D/0x2E liegt (Andriy/Discord bestätigt).
```

---

## [NEU] UI — Taste + LED-Ring

Einfügen **anstelle** des Display-Abschnitts (Waveshare ESP32) oder als eigener Abschnitt darunter. Das ESP32-Display kommt beim Lounge nicht zum Einsatz.

```markdown
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

Hardware-PWM (GPIO 12/13) kollidiert auf dem Pi 4 mit dem I²S-Clock-Generator — **nicht verwenden**. Stattdessen Software-PWM über `pigpio` (DMA-basiert, CPU-schonend). Für LED-Dimming reichen 200–500 Hz — `pigpio` liefert das problemlos auf jedem GPIO.
```

---

## [NEU] GPIO-Gesamtbelegung

Einfügen als neue Sektion unter **Hardware-Plattform**, z. B. nach dem Stack-Abschnitt.

```markdown
### GPIO-Gesamtbelegung — Raspberry Pi 4

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
```

---

## [GEÄNDERT] Offene Punkte — Hardware

Aktualisierte Einträge für die Hardware-Checkliste:

```markdown
### Hardware
- [x] Bestellung bei Tindie zusammenstellen (1× Louder Hat Plus 2X + 1× Louder Hat 1X non-Plus)
- [x] 24 V / 5 A Netzteil + DC-Buchse
- [x] USB-S/PDIF-Adapter für TOSLINK-Eingang aussuchen
- [ ] Soft-Start-MOSFET vorbereiten
- [ ] Kühlkonzept (Lüfter / Belüftungsschlitze im Gehäuse)
- [x] Original-UI reverse-engineered: 7-Pin-Stecker, Transistoren Q101–Q103 on-board, Pinbelegung dokumentiert
- [x] Lochrasterplatine bestücken: 7 Lötbrücken UI-Board → Pi-Header (keine aktiven Bauteile)
- [ ] UI-Board Funktion testen (LEDs + Taster) vor Einbau ins Gehäuse
```

---

## [GEÄNDERT] Offene Punkte — Software / Konfiguration

Neue Einträge am Ende der Software-Checkliste ergänzen:

```markdown
- [ ] `pigpio`-Service für LED-Steuerung + Taster-Handler einrichten
- [ ] PWM-Dimming für Gelb + Weiß (z. B. Standby = Gelb gedimmt, Active = Weiß voll)
- [ ] Taster-Logik definieren (Kurzdrück = Play/Pause, Langdrück = Standby/Wakeup o. ä.)
```
