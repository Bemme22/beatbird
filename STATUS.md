# BeatBird — Project Status

> Last updated: 2026-05-06

## Active speakers

| Speaker | Repo | Status | OS |
|---|---|---|---|
| Beat #1 | beatbird-display (old) | ✅ Production | Bookworm |
| Zipp Mini 2 | beatbird (new, v2.1.0) | 🔧 Services running, sound+display pending | Trixie |

## Install fixes committed (v2.1.1)

All five workarounds from Zipp Mini 2 first boot are now in the repo:

1. ✅ `install/30-camilladsp.sh`: version 4.0.0→4.1.2, arch suffix `aarch64` (not `aarch64-linux-gnu`)
2. ✅ `install/40-go-librespot.sh`: removed `--version` check (binary doesn't support it)
3. ✅ `install/_lib.sh`: new `ensure_module_loaded` helper — adds module to both `dtoverlay` and `/etc/modules` (Trixie compat)
4. ✅ `install/70-bridge.sh`: creates `/var/lib/beatbird` (required by systemd `ReadWritePaths`)
5. ✅ `config/systemd/go-librespot.service.tpl`: no CLI flags, reads `~/.config/go-librespot/` automatically

## Lounge — UI reverse-engineering complete

- Original 7-pin UI board documented: button + 3-colour LED ring (R/Y/W)
- Transistors Q101–Q103 on-board → Pi only drives GPIO bases
- GPIO mapping finalised (17=button, 22/23/24=LEDs)
- Perfboard layout: 7 solder bridges, no active components
- Full documentation in `docs/Lounge.md`
- Blocker: custom DT overlay for 3-DAC stack (Sonocotta)

## Architecture

- CamillaDSP volume = single source of truth
- All sources → hw:Loopback,0 → CamillaDSP reads hw:Loopback,1
- Profile YAML = one file per speaker
- Source handoff = mutual-kill
- BT volume via BlueALSA Manager1.GetPCMs (uint16: (L<<8)|R, 0..127)

## Roadmap

- [ ] Soundcheck + display test on Zipp Mini 2
- [ ] REW measurement → custom DSP config
- [ ] Genre-EQ presets via PatchConfig
- [ ] Audio feedback sounds
- [ ] ESP32 main.cpp review (1168 lines)
- [ ] Snapcast multi-room
- [ ] MOSFET soft-start for Beat #1
- [ ] Lounge: UI board function test (LEDs + button)
- [ ] Lounge: `pigpio` service for LED dimming + button handler
