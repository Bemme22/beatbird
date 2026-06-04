# Reaching a BeatBird speaker on the LAN

How to address a speaker from a laptop/phone, and why `<host>.local` may not be
the right answer on every network. (Network-specific addresses — IPs, SSID — are
kept out of this public repo; put them in gitignored `secrets/`.)

## TL;DR

Prefer, in order:

1. **Router DNS / the plain hostname** — most home routers (e.g. AVM FRITZ!Box)
   run a DNS server that resolves every DHCP client by name. `<hostname>` or
   `<hostname>.<router-suffix>` (FRITZ!Box: `<hostname>.fritz.box`) resolves via
   plain **unicast DNS** — reliable, no multicast, no avahi, no IPv6 quirks.
2. **The IP address** — universal, zero DNS. Pin it with a **DHCP reservation**
   on the router (or `secrets/static-ip.conf` → `install/25-static-ip.sh`) so it
   never rotates.
3. **`<host>.local` (mDNS/avahi)** — convenient but the least reliable here; see
   below.

## Why `.local` / mDNS can fail even when the speaker is online

mDNS resolution depends on link-local **multicast** reaching the client, and on
the client picking a usable record from the reply. Two failure modes seen in the
field:

- **Only the IPv6 link-local (`fe80::…`) comes back, no IPv4 A record.** avahi by
  default publishes both an `A` (the LAN IPv4) and an `AAAA` (the interface's
  `fe80::` link-local). On at least one LAN, the IPv4 mDNS path did not traverse
  to the (Windows) client while the IPv6 path did — so `<host>.local` resolved
  **only** to `fe80::…`, which nothing can actually connect to → "speaker
  unreachable" despite a perfectly good DHCP lease.
- **The lease itself dropped** (a different problem) — then there's no A record
  to publish at all. WiFi power-save dropping DHCP/multicast is a common cause;
  see `install/20-wifi.sh` (watchdog + power-save-off).

`install/00-base.sh` sets avahi to IPv4-only (`use-ipv6=no`,
`publish-aaaa-on-ipv4=no`) so `.local` at least never resolves to a dead
`fe80::`. But note: if a network's IPv4 mDNS doesn't traverse at all, IPv4-only
avahi makes `.local` resolve to *nothing* rather than to a useless `fe80::` —
neither is reachable, so this is cleanup, **not** a guaranteed fix. On such a
network, use router DNS or the IP (above).

## The pairing QR uses the IP, not `.local`

For the same reason, the BT-pairing QR the bridge renders points at
`http://<ip>:<port>/` (the runtime IP from `system.ip_address()`), not
`http://<host>.local:…`. A phone scanning it then needs no DNS at all. It falls
back to `<host>.local` only if the IP can't be read yet.

## Note on the WiFi stack

The power-save-off + watchdog in `install/20-wifi.sh` assume the
NetworkManager keyfile this repo writes (`beatbird.nmconnection`). A speaker
provisioned via **netplan** (or any other connection name) won't be covered by
that path — power-save would have to be set on the *active* connection / the
netplan YAML instead. Check `nmcli -t -f NAME,DEVICE connection show --active`
before assuming the keyfile is in use.
