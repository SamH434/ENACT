"""
Wifi collector: current link state + nearby AP scan via netsh.

Two distinct sets of records per cycle:
1. Connection state: SSID, BSSID, channel, signal, link rate of the AP
   we're currently associated with (tells us about our own link health)
2. Nearby AP inventory: every AP the radio can see, with channel and
   signal. Tells us about the RF environment (congestion, channel
   overlap, new APs appearing)

((Entirely passive))
The radio listens for beacon frames that nearby access
points broadcast on their own (same thing phone does when you open
Wifi settings). So to clarify, no transmission, no probing, 
no scanning in the illegal sense

FYI: approximate dBm using the conventional formula:
    dBm ≈ (percent / 2) - 100
So 100% ≈ -50 dBm (excellent), 50% ≈ -75 dBm (usable), 20% ≈ -90 dBm
(We keep the raw percentage in metadata too)
"""

import re
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord


# generic key/value line: "Key Name: value"
_KV_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.+?)\s*$", re.MULTILINE)
_PCT_RE = re.compile(r"(\d+)\s*%")

# converts a Windows signal percentage to approximate dBm using the standard formula
def _signal_percent_to_dbm(percent: int) -> int:
    """Windows-conventional approximation. 100% ≈ -50 dBm, 0% ≈ -100 dBm."""
    return (percent // 2) - 100

"""
Reports the current Wi-Fi link state and inventories every nearby access point.

Runs two netsh commands per cycle: 
'show interfaces' for our current AP
'show networks mode=bssid' for everything visible.
"""
class WifiCollector(Collector):
    name = "wifi"

    # runs both netsh commands and combines their records into a single list
    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        records.extend(self._collect_current_connection())
        records.extend(self._collect_nearby_aps())
        return records

    # ---------- current connection 

    # collects the current connection state: RSSI, link rate, channel info
    def _collect_current_connection(self) -> list[TelemetryRecord]:
        output = self._run_netsh(["netsh", "wlan", "show", "interfaces"])
        if output is None:
            return []

        fields = self._parse_kv(output)
        state = fields.get("State", "").lower()

        # if Wifi is off or disconnected, return one honest "no data" record
        if state != "connected":
            self.log.info("wlan interface state: %s (not connected)", state or "unknown")
            return [TelemetryRecord(
                collector=self.name,
                metric="connection_state",
                value=state or "unknown",
                metadata={"connected": False},
            )]

        ssid = fields.get("SSID")
        bssid = fields.get("BSSID")
        channel_str = fields.get("Channel")
        signal_str = fields.get("Signal")
        link_rx = fields.get("Receive rate (Mbps)")
        link_tx = fields.get("Transmit rate (Mbps)")

        signal_pct = self._extract_percent(signal_str) if signal_str else None
        channel = int(channel_str) if channel_str and channel_str.isdigit() else None
        rx_mbps = float(link_rx) if link_rx else None
        tx_mbps = float(link_tx) if link_tx else None

        records: list[TelemetryRecord] = []

        # current_rssi_dbm: our actual signal strength to the AP we're using
        if signal_pct is not None:
            records.append(TelemetryRecord(
                collector=self.name,
                metric="current_rssi_dbm",
                value=_signal_percent_to_dbm(signal_pct),
                metadata={
                    "ssid": ssid,
                    "bssid": bssid,
                    "channel": channel,
                    "signal_pct": signal_pct,
                },
            ))

        # current_link_mbps: take the lower of rx/tx as the headline number, both stay in metadata
        if rx_mbps is not None or tx_mbps is not None:
            link_mbps = min(v for v in (rx_mbps, tx_mbps) if v is not None)
            records.append(TelemetryRecord(
                collector=self.name,
                metric="current_link_mbps",
                value=link_mbps,
                metadata={
                    "ssid": ssid,
                    "bssid": bssid,
                    "rx_mbps": rx_mbps,
                    "tx_mbps": tx_mbps,
                },
            ))

        return records

    # ---------- nearby APs 

    # scans every nearby AP and emits one record per visible BSSID
    def _collect_nearby_aps(self) -> list[TelemetryRecord]:
        output = self._run_netsh(
            ["netsh", "wlan", "show", "networks", "mode=bssid"]
        )
        if output is None:
            return []

        aps = self._parse_networks(output)
        records: list[TelemetryRecord] = []

        # we key on BSSID not SSID: one SSID can have many BSSIDs (mesh routers,
        # office buildings with several APs on the same network name), each one
        # is a separate physical radio worth tracking on its own
        for ap in aps:
            signal_pct = ap.get("signal_pct")
            # skip APs with no signal reading: partial netsh probes aren't useful telemetry
            if signal_pct is None:
                continue
            records.append(TelemetryRecord(
                collector=self.name,
                metric="nearby_ap",
                value=_signal_percent_to_dbm(signal_pct),
                metadata={
                    "ssid": ap.get("ssid"),
                    "bssid": ap.get("bssid"),
                    "channel": ap.get("channel"),
                    "signal_pct": signal_pct,
                    "auth": ap.get("auth"),
                },
            ))

        return records

    # ---------- helpers 

    # runs a netsh command and returns stdout, or None if it failed
    def _run_netsh(self, cmd: list[str]) -> str | None:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
        except subprocess.TimeoutExpired:
            self.log.warning("netsh command timed out: %s", " ".join(cmd))
            return None
        except FileNotFoundError:
            self.log.error("netsh not found (not a Windows system?)")
            return None
        return result.stdout

    # parses simple "Key : value" output (like show interfaces) into a flat dict
    def _parse_kv(self, output: str) -> dict[str, str]:
        """Parse 'Key : value' lines into a flat dict."""
        fields: dict[str, str] = {}
        for match in _KV_RE.finditer(output):
            key, value = match.group(1), match.group(2)
            # Only take the first occurrence of each key (for show interfaces)
            if key not in fields:
                fields[key] = value
        return fields

    # pulls the first percentage value out of a string like "Signal: 87%"
    def _extract_percent(self, text: str) -> int | None:
        m = _PCT_RE.search(text)
        return int(m.group(1)) if m else None

    # parses the nested 'show networks mode=bssid' output into a list of AP dicts
    def _parse_networks(self, output: str) -> list[dict]:
        """Parse 'netsh wlan show networks mode=bssid' into a list of AP dicts.

        Output structure (heavily abbreviated):
            SSID 1 : MyNetwork
                Authentication : WPA2-Personal
                BSSID 1 : aa:bb:cc:dd:ee:ff
                    Signal : 87%
                    Channel : 6
                BSSID 2 : aa:bb:cc:dd:ee:00
                    Signal : 42%
                    Channel : 6
            SSID 2 : NeighborWifi
                ...

        We walk line by line, tracking current SSID and BSSID context, and emit
        one dict per BSSID. Crude state machine, but it works.
        """
        aps: list[dict] = []
        current_ssid: str | None = None
        current_auth: str | None = None
        current_ap: dict | None = None

        for raw_line in output.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue

            # SSID line at minimum indent: "SSID N : name"
            # starting a new SSID means flushing whatever AP we were building
            ssid_m = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line)
            if ssid_m:
                if current_ap is not None:
                    aps.append(current_ap)
                    current_ap = None
                current_ssid = ssid_m.group(1).strip()
                current_auth = None
                continue

            # BSSID line: "    BSSID N : mac"
            # starting a new BSSID also means flushing the previous one
            bssid_m = re.match(r"^\s+BSSID\s+\d+\s*:\s*([0-9a-fA-F:]+)\s*$", line)
            if bssid_m:
                if current_ap is not None:
                    aps.append(current_ap)
                current_ap = {
                    "ssid": current_ssid,
                    "bssid": bssid_m.group(1),
                    "auth": current_auth,
                    "signal_pct": None,
                    "channel": None,
                }
                continue

            # Authentication line: applies to the current SSID block
            # only capture it if we're NOT inside a BSSID yet, otherwise it's a
            # different "Authentication" line nested under the BSSID (encryption, etc.)
            auth_m = re.match(r"^\s+Authentication\s*:\s*(.+?)\s*$", line)
            if auth_m and current_ap is None:
                current_auth = auth_m.group(1).strip()
                continue

            # Signal / Channel: apply to current BSSID
            if current_ap is not None:
                sig_m = re.match(r"^\s+Signal\s*:\s*(\d+)\s*%\s*$", line)
                if sig_m:
                    current_ap["signal_pct"] = int(sig_m.group(1))
                    continue
                ch_m = re.match(r"^\s+Channel\s*:\s*(\d+)\s*$", line)
                if ch_m:
                    current_ap["channel"] = int(ch_m.group(1))
                    continue

        # Flush the last AP after the loop ends
        # loops that build records on a sentinel always need a final flush,
        # otherwise the last entry gets silently dropped
        if current_ap is not None:
            aps.append(current_ap)

        return aps


if __name__ == "__main__":
    import json

    collector = WifiCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))