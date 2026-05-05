# go-librespot configuration (rendered from profile)

device_name: "{{ DEVICE_NAME }}"
device_type: speaker

audio_backend: alsa
# Writes to ALSA Loopback → CamillaDSP reads from hw:Loopback,1
audio_device: "hw:Loopback,0"

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
