#!/bin/bash
# beatbird-louder-hat-init — Louder Hat Plus 1X (single TAS5825M)
# Setzt die Amp-Pegel über Control-NAMEN (numids verschieben sich zwischen
# Treiber-Versionen).
#
# ⚠️ Die Control-Namen hängen am OVERLAY-MODUS, nicht an der Treiberversion:
#    stereo BTL  ->  '2.0 Digital', '2.0 Analog Gain', …
#    PBTL mono   ->  'Digital Volume', 'Analog Gain', …   (ohne Präfix)
#    Bis 17.09.2026 setzte dieses Skript ausschließlich die '2.0 '-Namen und
#    schluckte den Fehler (`|| true`). Auf RobinPi (PBTL) hat es damit seit dem
#    Bring-up NICHTS getan — der Chip lief auf seinen Treiber-Defaults, und das
#    Profil beschrieb einen Pegel, den niemand gesetzt hatte. Deshalb sucht
#    set_q den Namen jetzt in beiden Schreibweisen und MELDET, wenn keine passt.
#    Ein stiller Fehlschlag in der Pegelkette ist unauffindbar: er klingt nur
#    leiser (oder lauter) und sieht nirgends nach Fehler aus.
#
# @ANALOG_GAIN@ / @DIGITAL@ werden beim Install aus dem Profil eingesetzt
# (install/10-soundcard/louder-hat-plus-1x.sh) — dieses Skript läuft als
# systemd-oneshot beim Boot und hat dort kein Profil zur Hand.

CARD=LouderRaspberry
MAX_TRIES=60

for i in $(seq 1 $MAX_TRIES); do
  amixer -c "$CARD" scontents >/dev/null 2>&1 && break
  sleep 0.5
done
amixer -c "$CARD" scontents >/dev/null 2>&1 || {
  echo "louder-hat-init: $CARD nicht gefunden nach $MAX_TRIES Versuchen" >&2
  exit 1; }

fehler=0

# set_q <wert> <name...> — setzt das erste Control, das es unter einem der
# Namen gibt, jeweils mit und ohne '2.0 '-Präfix. Findet sich keiner, ist das
# eine Meldung wert — nicht ein stilles `true`.
set_q() {
  local wert="$1" basis name
  shift
  for basis in "$@"; do
    for name in "2.0 $basis" "$basis"; do
      amixer -c "$CARD" sget "$name" >/dev/null 2>&1 || continue
      if amixer -c "$CARD" -q sset "$name" "$wert" 2>/dev/null; then
        return 0
      fi
      echo "louder-hat-init: '$name' auf $wert setzen fehlgeschlagen" >&2
      fehler=1
      return 1
    done
  done
  echo "louder-hat-init: kein Control $* gefunden (auch nicht mit '2.0 '-Präfix)" >&2
  fehler=1
  return 1
}

# Digital Volume: im PBTL-Modus heißt es 'Digital Volume', im Stereo-Modus
# '2.0 Digital'. @DIGITAL@ = 103 entspricht Register 0x4C = 0x30 = 0 dB (am
# 17.09.2026 per i2cget am Chip gegengeprüft — NICHT −6 dB, wie hier bis dahin
# als Kommentar stand). Schrittweite 0,5 dB, 127 wäre +12 dB.
set_q @DIGITAL@     'Digital Volume' 'Digital'
set_q @ANALOG_GAIN@ 'Analog Gain'
set_q Off           'Equalizer'      # chip-interne EQ aus — CamillaDSP macht das

# PBTL hat EINEN gebrückten Kanal und entsprechend ein einziges Gain-Control;
# im Stereo-Modus sind es zwei. Am Vorhandensein unterscheiden, nicht raten.
if amixer -c "$CARD" sget 'Mono Channel Gain' >/dev/null 2>&1; then
  set_q 0 'Mono Channel Gain'
else
  set_q 0 'Channel Left Gain'
  set_q 0 'Channel Right Gain'
fi

# Rücklesen und loggen: die gesetzten Pegel sind die halbe Lautstärke-Bilanz
# des Speakers und gehören ins Boot-Journal, nicht in die Annahme.
for c in 'Digital Volume' 'Digital' 'Analog Gain'; do
  ist=$(amixer -c "$CARD" sget "$c" 2>/dev/null | grep -oE '[0-9]+ \[[0-9]+%\].*' | head -1)
  [[ -n "$ist" ]] && echo "louder-hat-init:   $c = $ist"
done

if [[ $fehler -ne 0 ]]; then
  echo "louder-hat-init: $CARD konfiguriert (Plus 1X) — MIT FEHLERN, s. oben" >&2
  exit 1
fi
echo "louder-hat-init: $CARD konfiguriert (Plus 1X)"
