"""
beatbird.sources.snapcast — lightweight Snapserver TCP-control client.

The bridge uses this to detect when audio is actively being pushed via
Snapcast (Music Assistant → snapserver pipe → snapclient → ALSA). When
detected, source is set to SNAPCAST so the display marker switches to
the multiroom purple.

We don't subscribe to the snapserver's notification stream — a periodic
poll every few seconds is plenty for source-indicator latency, and a
simple request/response avoids holding a long-lived TCP connection.
"""

from __future__ import annotations

import json
import logging
import socket
import time

log = logging.getLogger("beatbird.snapcast")


class SnapcastClient:
    """Polling client for the Snapserver TCP control port (default 1705).

    Args:
        host: Snapserver host. Read from BEATBIRD_SNAPCAST_SERVER env or
            the profile.
        port: TCP-control port (snapserver default 1705).
        my_mac: Our snapclient's MAC address — used to find our client
            entry in the server's GetStatus response. Lowercase, colon-
            separated (e.g. "6c:4c:bc:db:68:b7").
        timeout: socket timeout in seconds.
    """

    def __init__(self, host: str, port: int = 1705,
                 my_mac: str = "", timeout: float = 1.5):
        self.host = host
        self.port = port
        self.my_mac = my_mac.lower()
        self.timeout = timeout

    def _rpc(self, method: str, params: dict | None = None) -> dict | None:
        """Send one JSON-RPC request, return the parsed result or None."""
        msg = {"id": 1, "jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=self.timeout) as s:
                s.sendall((json.dumps(msg) + "\r\n").encode())
                # Snapserver replies with one line of JSON + newline; read
                # until we hit the terminator (or the connection closes).
                buf = bytearray()
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if b"\n" in buf:
                        break
                if not buf:
                    return None
                return json.loads(buf.decode("utf-8", "replace").split("\n", 1)[0])
        except (OSError, json.JSONDecodeError) as e:
            log.debug("snapserver RPC %s failed: %s", method, e)
            return None

    def is_playing_for_us(self) -> bool:
        """True iff our snapclient is currently in a group whose stream
        is in 'playing' status. Looks up our client by MAC."""
        if not self.host or not self.my_mac:
            return False
        resp = self._rpc("Server.GetStatus")
        if not resp:
            return False
        server = resp.get("result", {}).get("server", {})
        groups = server.get("groups", []) or []
        streams = server.get("streams", []) or []
        stream_by_id = {s.get("id"): s for s in streams}

        for g in groups:
            stream_id = g.get("stream_id")
            stream = stream_by_id.get(stream_id) or {}
            if stream.get("status") != "playing":
                continue
            # This group is actively streaming. Check if we're in it.
            for c in g.get("clients", []) or []:
                host = c.get("host", {}) or {}
                mac = (host.get("mac") or "").lower()
                if mac == self.my_mac and c.get("connected"):
                    return True
        return False


def get_local_wlan_mac() -> str:
    """Return the lowercase MAC of the first up wlan/eth interface.
    Used by the bridge to identify itself in Snapserver's client list."""
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) > 1 and parts[1] == "00000000":
                    iface = parts[0]
                    with open(f"/sys/class/net/{iface}/address") as af:
                        return af.read().strip().lower()
    except OSError:
        pass
    return ""
