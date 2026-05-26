# go-librespot configuration (rendered from profile)

device_name: "{{ DEVICE_NAME }}"
device_type: speaker

audio_backend: alsa
# Writes to the beatbird_mix dmix (defined in /etc/asound.conf), which
# routes through to ALSA Loopback's playback side. Going via the dmix
# instead of raw hw:Loopback,0 lets the bridge's UI SFX aplay share
# the same playback stream — without dmix, librespot held Loopback
# exclusively and SFX during music was silent.
audio_device: "beatbird_mix"
# Default ALSA buffer is 500ms — that's the drain time after a pause, so
# tap → silent has up to ~0.5s lag. 100ms keeps pause snappy; CamillaDSP
# downstream adds its own ~85ms (chunksize 1024 @ 48k). Verified stable on
# Pi Zero 2W; if you hear underruns on weaker hardware, bump to 150–200ms.
audio_buffer_time: 100000   # µs
audio_period_count: 4       # → ~25ms periods

zeroconf_enabled: true
zeroconf_backend: avahi

credentials:
  type: zeroconf
  zeroconf:
    persist_credentials: true

server:
  enabled: true
  address: "0.0.0.0"
  port: 3678

normalisation_enabled: {{ NORMALISATION }}
bitrate: {{ BITRATE }}

# Spotify volume is kept in sync with CamillaDSP by the bridge.
# We start at max; the bridge adjusts both on every change.
volume:
  initial: 65535
