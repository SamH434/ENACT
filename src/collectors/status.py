"""
Connectivity status collector: high-level "is the network working" checks.

Distinct from the other collectors in that it doesn't produce raw telemetry
(latency, RSSI, etc.). Instead, it aggregates observable state into three
plain-english readouts optimized for the dashboard's status panel:

    wifi_status:     the SSID we're associated with, or "disconnected"
    internet_status: composite of DNS + ping, classifies as "ok" / "degraded"
                     / "no_dns" / "no_route" / "down"
    vpn_status:      whether any non-physical tunnel adapter is active

"""

import re
import socket
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord


# how many recent DNS/connectivity samples to consider when composing status
# also the ping target for the internet-reachability check
INTERNET_CHECK_HOST = "1.1.1.1"
INTERNET_CHECK_TIMEOUT_MS = 1500

VPN_ADAPTER_HINTS = [
    "wintun", "tap-windows", "tap adapter", "openvpn",
    "wireguard", "tailscale", "vpn", "tunnel",
    "nordlynx", "expressvpn", "protonvpn", "cisco anyconnect",
    "forticlient", "globalprotect", "zerotier",
]

# adapter names that are physical/local, not VPN. used to exclude false positives
# from the VPN detection above
PHYSICAL_ADAPTER_HINTS = [
    "wi-fi", "wireless", "ethernet", "bluetooth", "loopback",
]


"""
Collects three high-level connectivity readouts every cycle.

Runs three checks in sequence: wifi (via netsh), internet (DNS + ping),
and VPN (via ipconfig). Each produces one TelemetryRecord. The dashboard
reads these as the source of truth for its status panel.
"""
class StatusCollector(Collector):
    name = "status"

    # runs one status check cycle and emits three records
    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        records.append(self._check_wifi())
        records.append(self._check_internet())
        records.append(self._check_vpn())
        return records

    # checks Wi-Fi association state via netsh wlan show interfaces
    def _check_wifi(self) -> TelemetryRecord:
        output = self._run(["netsh", "wlan", "show", "interfaces"])
        if output is None:
            return TelemetryRecord(
                collector=self.name,
                metric="wifi_status",
                value="unavailable",
                metadata={"connected": False, "reason": "netsh unavailable"},
            )

        # parse "State" and "SSID" fields. netsh output is key-value lines
        state_m = re.search(r"^\s*State\s*:\s*(.+?)\s*$", output, re.MULTILINE)
        ssid_m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", output, re.MULTILINE)
        state = state_m.group(1).strip().lower() if state_m else "unknown"
        ssid = ssid_m.group(1).strip() if ssid_m else None

        if state == "connected" and ssid:
            return TelemetryRecord(
                collector=self.name,
                metric="wifi_status",
                value="connected",
                metadata={"connected": True, "ssid": ssid},
            )
        return TelemetryRecord(
            collector=self.name,
            metric="wifi_status",
            value="disconnected",
            metadata={"connected": False, "state": state},
        )

    # composite check: can we resolve DNS AND reach an internet endpoint?
    def _check_internet(self) -> TelemetryRecord:
        dns_ok = self._check_dns_resolution()
        ping_ok = self._check_ping()

        if dns_ok and ping_ok:
            status = "ok"
            summary = "DNS + ping succeeded"
        elif dns_ok and not ping_ok:
            # DNS works but ping fails. probably ICMP filtered, not total outage.
            # honest classification is "degraded" not "down"
            status = "degraded"
            summary = "DNS works, ICMP blocked or dropped"
        elif not dns_ok and ping_ok:
            # ping works (raw IP) but DNS doesn't resolve. resolver problem
            status = "no_dns"
            summary = "reachable by IP but DNS not resolving"
        else:
            status = "down"
            summary = "no DNS, no ping"

        return TelemetryRecord(
            collector=self.name,
            metric="internet_status",
            value=status,
            metadata={
                "dns_ok": dns_ok,
                "ping_ok": ping_ok,
                "check_host": INTERNET_CHECK_HOST,
                "summary": summary,
            },
        )

    # detects whether any active network adapter looks like a VPN tunnel
    def _check_vpn(self) -> TelemetryRecord:
        output = self._run(["ipconfig", "/all"])
        if output is None:
            return TelemetryRecord(
                collector=self.name,
                metric="vpn_status",
                value="unavailable",
                metadata={"connected": False, "reason": "ipconfig unavailable"},
            )

        # ipconfig groups output into sections per adapter. we split by blank lines
        # or section headers and inspect each section for VPN indicators
        adapters = self._parse_ipconfig_adapters(output)
        vpn_adapters = []
        for adapter in adapters:
            name_lower = adapter["name"].lower()
            # skip physical adapters
            if any(hint in name_lower for hint in PHYSICAL_ADAPTER_HINTS):
                continue
            # match against known VPN adapter naming patterns
            if any(hint in name_lower for hint in VPN_ADAPTER_HINTS):
                # only count if the adapter is actually up with an IP
                if adapter["ipv4"]:
                    vpn_adapters.append({
                        "name": adapter["name"],
                        "ipv4": adapter["ipv4"],
                    })

        if vpn_adapters:
            return TelemetryRecord(
                collector=self.name,
                metric="vpn_status",
                value="connected",
                metadata={
                    "connected": True,
                    "adapters": vpn_adapters,
                    "count": len(vpn_adapters),
                },
            )
        return TelemetryRecord(
            collector=self.name,
            metric="vpn_status",
            value="none",
            metadata={"connected": False},
        )

    # tries to resolve a well-known hostname, returns True if DNS is working
    def _check_dns_resolution(self) -> bool:
        try:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(2.0)
            try:
                socket.gethostbyname("cloudflare.com")
                return True
            finally:
                socket.setdefaulttimeout(old_timeout)
        except (socket.gaierror, socket.timeout, OSError):
            return False

    # sends one ping to the check host, returns True if it succeeded
    def _check_ping(self) -> bool:
        try:
            result = subprocess.run(
                ["ping", INTERNET_CHECK_HOST, "-n", "1",
                 "-w", str(INTERNET_CHECK_TIMEOUT_MS)],
                capture_output=True,
                text=True,
                timeout=(INTERNET_CHECK_TIMEOUT_MS / 1000) + 2,
            )
            # ping returns 0 on any reply, non-zero on complete failure
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # generic subprocess runner with graceful failure handling
    def _run(self, cmd: list[str]) -> str | None:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            self.log.warning("command failed: %s: %s", " ".join(cmd), e)
            return None

    # walks the ipconfig /all output and returns one dict per adapter section
    def _parse_ipconfig_adapters(self, output: str) -> list[dict]:
        """Parse ipconfig /all output into a list of adapter dicts.

        We split on lines that look like adapter headers ("X adapter Y:") and
        pull out the name plus any IPv4 address inside the section.
        """
        adapters: list[dict] = []
        current: dict | None = None

        for line in output.splitlines():
            # adapter header line: "Wi-Fi adapter Wi-Fi:" or "Ethernet adapter Ethernet:"
            header_m = re.match(r"^([A-Za-z0-9 \-]+adapter\s+.+?):\s*$", line)
            if header_m:
                if current is not None:
                    adapters.append(current)
                current = {"name": header_m.group(1).strip(), "ipv4": None}
                continue

            if current is None:
                continue

            # look for IPv4 Address lines. handle both "192.168.1.42" and
            # "192.168.1.42(Preferred)" forms
            ipv4_m = re.search(
                r"IPv4 Address[.\s]+:\s*(\d+\.\d+\.\d+\.\d+)", line
            )
            if ipv4_m:
                current["ipv4"] = ipv4_m.group(1)

        if current is not None:
            adapters.append(current)
        return adapters


if __name__ == "__main__":
    # manual test: python -m src.collectors.status
    import json

    collector = StatusCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))