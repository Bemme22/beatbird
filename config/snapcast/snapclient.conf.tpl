# /etc/default/snapclient — rendered from profile
START_SNAPCLIENT=true
# beatbird_mix is the dmix-on-Loopback PCM defined in
# /etc/asound.conf — same name go-librespot writes to. Routing
# snapclient through the same dmix lets the two sources share the
# Loopback playback substream that CamillaDSP captures from. Without
# this, snapclient grabbed a different Loopback substream and
# CamillaDSP heard silence even though the connection was live.
#
# --mixer none: snapclient does NOT attenuate. Volume lives only in
# CamillaDSP; the bridge adopts the MA/HA per-client slider into the
# CamillaDSP fader (bridge._sync_snapcast_volume). With the default
# software mixer (exp base 10: 25 % slider = 8.6 % gain) the two stages
# multiplied, and "100 %" meant something different on every speaker.
SNAPCLIENT_OPTS="--host {{ SERVER }} --latency {{ LATENCY }} --hostID {{ HOSTNAME }} --soundcard beatbird_mix --mixer none"
