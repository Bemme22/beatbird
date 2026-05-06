# BeatBird — Project Status

> Last updated: 2026-05-05

## Active speakers

| Speaker | Repo | Status | OS |
|---|---|---|---|
| Beat #1 | beatbird-display (old) | ✅ Production | Bookworm |
| Zipp Mini 2 | beatbird (new, v2.1.0) | 🔧 Services running, sound+display pending | Trixie |

## Install workarounds applied on Zipp Mini 2 (not yet committed)

1. install/30-camilladsp.sh: version 4.0.0→4.1.2, arch: aarch64 not aarch64-linux-gnu
2. install/40-go-librespot.sh: remove --version check, just check binary exists
3. snd-aloop in /etc/modules (dtoverlay doesn't work on Trixie)
4. /var/lib/beatbird must be created by install/70-bridge.sh
5. go-librespot service: no CLI flags, reads ~/.config/go-librespot/ automatically

## Architecture

- CamillaDSP volume = single source of truth
- All sources → hw:Loopback,0 → CamillaDSP reads hw:Loopback,1
- Profile YAML = one file per speaker
- Source handoff = mutual-kill
- BT volume via BlueALSA Manager1.GetPCMs (uint16: (L<<8)|R, 0..127)

## Roadmap

- [ ] Soundcheck + display test on Zipp Mini 2
- [ ] REW measurement → custom DSP config
- [ ] Commit install fixes to repo
- [ ] Genre-EQ presets via PatchConfig
- [ ] Audio feedback sounds
- [ ] ESP32 main.cpp review (1168 lines)
- [ ] Snapcast multi-room
- [ ] MOSFET soft-start for Beat #1