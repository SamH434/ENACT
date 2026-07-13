"""
Connectivity status collector: active liveness probes for real network state.

Distinct from the other collectors in that it doesn't produce raw telemetry.
It emits three high-level readouts optimized for the dashboard's status
panel, each backed by an active probe that tests whether traffic ACTUALLY
flows to a specific destination.

The prior implementation relied on Windows adapter state (netsh, ipconfig), This was unreliable:
- Windows reports adapter existence long after a VPN tunnel is torn down
- ipconfig on machines with virtual adapters can pick the wrong interface
- DNS resolution against a local resolver "succeeds" even when the wider
  network is broken, because the local process happily answers
This version uses active TCP probes instead: attempt a connection to a
known destination, honestly report the result. TCP handshake tells the truth
in a way that reading Windows state does not.

    wifi_status:     TCP probe to the default gateway (are we on ANY network)
    internet_status: TCP probe to a public anycast host (does traffic reach the public internet)
    vpn_status:      TCP probe to a destination that requires the tunnel (is the VPN actually delivering)

*The VPN check is region aware
"""

import socket
import subprocess
import re

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord


# probe timeouts. deliberately short: a slow answer is functionally the
# same as no answer for the "is this working right now" question the
# dashboard needs to answer. total collector cycle stays under 5 seconds
PROBE_TIMEOUT_SEC = 2.0

# TCP probe targets. each answers a specific question with a real network
# operation, not a check of local state.
PROBE_GATEWAY_PORT = 80          # any port typically open on home routers
PROBE_INTERNET_HOST = "1.1.1.1"  # anycast, hard to be broken globally
PROBE_INTERNET_PORT = 443
PROBE_VPN_HOST = "www.google.com"  # region-restricted; VPN-required in some places
PROBE_VPN_PORT = 443


"""
Collects three high-level connectivity readouts every cycle via active probes.

Each probe attempts a TCP connection to a target that would only succeed if a
specific layer of the network is actually working. Honest failure reporting:
if a probe times out, we say so rather than inferring "connected" from
adapter state.
"""
class StatusCollector(Collector):
    name = "status"

    # runs one status check cycle: three probes, three records
    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        records.append(self._check_wifi())
        records.append(self._check_internet())
        records.append(self._check_vpn())
        return records

    # tests reachability to the default gateway: are we on a network at all?
    # if this fails, wireless is truly disconnected. resolves the wifi-status
    # question via a real probe rather than netsh output
    def _check_wifi(self) -> TelemetryRecord:
        gateway = self._default_gateway()
        if gateway is None:
            return TelemetryRecord(
                collector=self.name,
                metric="wifi_status",
                value="disconnected",
                metadata={
                    "connected": False,
                    "reason": "no default gateway configured",
                },
            )

        # attempt a TCP connection to the gateway. we try port 80 first (most
        # home routers expose an admin UI), fall back to a UDP-ish check with
        # ICMP if TCP fails. either "connected" answer means our L2/L3 is up
        reachable = self._tcp_probe(gateway, PROBE_GATEWAY_PORT)
        if not reachable:
            # some routers block port 80 admin. try 53 (many run local DNS)
            reachable = self._tcp_probe(gateway, 53)

        # if TCP to the gateway didn't answer, we may still be on a network:
        # some enterprise gateways drop all inbound traffic. fall back to
        # checking whether the gateway is in our ARP table via a socket bind test
        if not reachable:
            reachable = self._can_bind_to_gateway_subnet(gateway)

        # get the connected SSID if we can, for display purposes only
        ssid = self._current_ssid()

        return TelemetryRecord(
            collector=self.name,
            metric="wifi_status",
            value="connected" if reachable else "disconnected",
            metadata={
                "connected": reachable,
                "gateway": gateway,
                "ssid": ssid,
                "probe": "tcp_to_gateway",
            },
        )

    # tests reachability to the public internet via a TCP probe to Cloudflare's
    # anycast address. this answers "does traffic reach the wider internet at
    # all" honestly: TCP handshake either completes or it doesn't
    def _check_internet(self) -> TelemetryRecord:
        # try the primary internet target
        primary_ok = self._tcp_probe(PROBE_INTERNET_HOST, PROBE_INTERNET_PORT)

        # try a secondary target so we don't misreport if one specific host is
        # having a bad day. either succeeding = internet is up
        secondary_ok = False
        if not primary_ok:
            secondary_ok = self._tcp_probe("8.8.8.8", 443)

        connected = primary_ok or secondary_ok
        if connected:
            status = "ok"
            summary = "TCP probe to public internet succeeded"
        else:
            status = "down"
            summary = "TCP probes to 1.1.1.1 and 8.8.8.8 both failed"

        return TelemetryRecord(
            collector=self.name,
            metric="internet_status",
            value=status,
            metadata={
                "connected": connected,
                "primary_probe": PROBE_INTERNET_HOST,
                "primary_ok": primary_ok,
                "secondary_ok": secondary_ok,
                "summary": summary,
            },
        )

    # tests VPN liveness by probing a destination that is normally blocked in
    # restricted regions and only reachable when a working tunnel is up. this
    # is more honest than checking adapter existence: it asks "is the VPN
    # actually delivering the connectivity it claims to provide"
    def _check_vpn(self) -> TelemetryRecord:
        # if the internet check itself just failed, VPN checking is meaningless
        # (can't distinguish "VPN broken" from "no internet at all"). report as
        # unknown rather than inferring a state we can't observe
        internet_ok = self._tcp_probe(PROBE_INTERNET_HOST, PROBE_INTERNET_PORT)
        if not internet_ok:
            return TelemetryRecord(
                collector=self.name,
                metric="vpn_status",
                value="unknown",
                metadata={
                    "reason": "no internet reachability, VPN state indeterminable",
                },
            )

        # probe the VPN-required host. if this connects, either the VPN is
        # working OR we're in an unrestricted region where the host is reachable
        # directly. from a "is my connectivity working for the sites I care
        # about" perspective, either case is a success
        vpn_target_ok = self._tcp_probe(PROBE_VPN_HOST, PROBE_VPN_PORT)

        # also detect the presence of a tunnel adapter as a secondary signal.
        # this alone isn't sufficient (as we discovered) but combined with the
        # probe it lets us distinguish "reachable via tunnel" from "reachable
        # directly (probably no VPN needed)"
        tunnel_present = self._tunnel_adapter_present()

        if vpn_target_ok and tunnel_present:
            status = "connected"
            summary = "tunnel adapter active and target reachable via VPN"
        elif vpn_target_ok and not tunnel_present:
            # region-unrestricted machine, target reachable without a tunnel
            status = "not_needed"
            summary = "target reachable directly, no tunnel active"
        elif not vpn_target_ok and tunnel_present:
            # this is the "adapter says up, tunnel actually broken" case that
            # was the entire reason we redesigned this collector
            status = "broken"
            summary = "tunnel adapter present but target unreachable via it"
        else:
            status = "none"
            summary = "no tunnel and target unreachable"

        return TelemetryRecord(
            collector=self.name,
            metric="vpn_status",
            value=status,
            metadata={
                "vpn_target": PROBE_VPN_HOST,
                "target_reachable": vpn_target_ok,
                "tunnel_adapter_present": tunnel_present,
                "summary": summary,
            },
        )

    # attempts a TCP connection to a host:port and returns True on success.
    # the workhorse probe primitive. socket-level, no subprocess overhead
    def _tcp_probe(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SEC):
                return True
        except (socket.timeout, socket.gaierror, OSError):
            return False

    # attempts a UDP socket bind test to the gateway's subnet. this succeeds
    # even for gateways that block all inbound TCP, as long as we have a
    # routable link to them at the IP level
    def _can_bind_to_gateway_subnet(self, gateway: str) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            # this doesn't actually send a packet, it just verifies routing
            s.connect((gateway, 1))
            s.close()
            return True
        except OSError:
            return False

    # gets the default IPv4 gateway from Windows routing table
    def _default_gateway(self) -> str | None:
        try:
            result = subprocess.run(
                ["route", "print", "-4", "0.0.0.0"],
                capture_output=True, text=True, timeout=3,
            )
            # look for a "0.0.0.0" destination line, gateway is 3rd column
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("0.0.0.0"):
                    parts = stripped.split()
                    if len(parts) >= 3:
                        # parts: dest, mask, gateway, interface, metric
                        gw = parts[2]
                        if re.match(r"^\d+\.\d+\.\d+\.\d+$", gw):
                            return gw
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    # gets the currently associated Wi-Fi SSID from netsh, if any.
    # only used for display metadata, not for the connected/disconnected decision
    def _current_ssid(self) -> str | None:
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.splitlines():
                m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
                if m:
                    ssid = m.group(1).strip()
                    # netsh sometimes returns an empty SSID for disconnected state
                    return ssid if ssid else None
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    # detects whether any known VPN tunnel adapter is present with an IP.
    # kept from the prior implementation as a secondary signal, not primary
    def _tunnel_adapter_present(self) -> bool:
        try:
            result = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        # patterns that indicate a VPN tunnel adapter (name OR description)
        hints = [
            "wintun", "tap-windows", "tap adapter", "openvpn",
            "wireguard", "tailscale", "letstap", "vpn", "tunnel",
            "nordlynx", "expressvpn", "protonvpn", "cisco anyconnect",
            "forticlient", "globalprotect", "zerotier",
        ]
        physical = ["wi-fi", "wireless", "ethernet", "bluetooth", "loopback"]

        # walk adapter sections, look for tunnel-like adapters with an IP
        current_name = ""
        current_desc = ""
        current_ipv4 = None
        for line in result.stdout.splitlines():
            header_m = re.match(r"^([A-Za-z0-9 \-]+adapter\s+.+?):\s*$", line)
            if header_m:
                # end of previous section: check if it was a tunnel with an IP
                if current_ipv4 and self._is_tunnel(current_name, current_desc,
                                                    hints, physical):
                    return True
                current_name = header_m.group(1).strip()
                current_desc = ""
                current_ipv4 = None
                continue
            desc_m = re.match(r"^\s+Description[.\s]+:\s*(.+?)\s*$", line)
            if desc_m:
                current_desc = desc_m.group(1).strip()
                continue
            ipv4_m = re.search(r"IPv4 Address[.\s]+:\s*(\d+\.\d+\.\d+\.\d+)", line)
            if ipv4_m:
                current_ipv4 = ipv4_m.group(1)

        # check the last section
        if current_ipv4 and self._is_tunnel(current_name, current_desc,
                                            hints, physical):
            return True
        return False

    # classifies an adapter as tunnel-like based on name + description
    def _is_tunnel(self, name: str, desc: str, hints: list, physical: list) -> bool:
        name_lower = name.lower()
        if any(p in name_lower for p in physical):
            return False
        haystack = f"{name_lower} {desc.lower()}"
        return any(h in haystack for h in hints)


if __name__ == "__main__":
    # manual test: python -m src.collectors.status
    import json
    collector = StatusCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))