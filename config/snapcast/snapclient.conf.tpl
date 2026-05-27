# /etc/default/snapclient — rendered from profile
START_SNAPCLIENT=true
# beatbird_mix is the dmix-on-Loopback PCM defined in
# /etc/asound.conf — same name go-librespot writes to. Routing
# snapclient through the same dmix lets the two sources share the
# Loopback playback substream that CamillaDSP captures from. Without
# this, snapclient grabbed a different Loopback substream and
# CamillaDSP heard silence even though the connection was live.
SNAPCLIENT_OPTS="--host {{ SERVER }} --latency {{ LATENCY }} --hostID {{ HOSTNAME }} --soundcard beatbird_mix"
