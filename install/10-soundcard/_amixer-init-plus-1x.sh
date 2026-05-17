#!/bin/bash
# beatbird-louder-hat-init — Louder Hat Plus 1X (single TAS5825M stereo)
# Setzt sichere Werte über Control-NAMEN (numids verschieben sich
# zwischen Treiber-Versionen). Fehlende Controls werden ignoriert.

CARD=LouderRaspberry
MAX_TRIES=60

for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done
amixer -c "$CARD" scontents >/dev/null 2>&1 || {
  echo "louder-hat-init: $CARD nicht gefunden nach $MAX_TRIES Versuchen" >&2
  exit 1; }

# Sichere Defaults — silent fail wenn Control bei der Treiberversion fehlt
set_q() { amixer -c "$CARD" -q sset "$1" "$2" 2>/dev/null || true; }

set_q '2.0 Digital Volume'  103     # ~-6 dB
set_q '2.0 Analog Gain'      25     # ~-3 dB von max
set_q '2.0 Equalizer'         0     # interne EQ aus (CamillaDSP macht das)
set_q '2.0 Channel L Gain'    0     # 0 dB
set_q '2.0 Channel R Gain'    0     # 0 dB

echo "louder-hat-init: $CARD konfiguriert (Plus 1X)"
