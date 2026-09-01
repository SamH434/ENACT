"""
Firewall posture collector

Windows has three profile types the firewall applies per active network:
    Domain:   corporate/Active Directory managed network
    Private:  home / trusted network
    Public:   coffee shop / untrusted network
"""

import re
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord

PROFILE_NAMES = ["Domain", "Private", "Public"]


"""
Collects Windows Defender Firewall posture: which profiles are enabled,
what the default inbound/outbound policies are, and rule counts.
"""
class FirewallCollector(Collector):
    name = "firewall"

    def collect(self) -> list[TelemetryRecord]:
        output = self._run_netsh()
        if output is None:
            return [self._unavailable_record()]

        profiles = self._parse_profiles(output)
        records: list[TelemetryRecord] = []

        enabled_count = 0
        for profile_name in PROFILE_NAMES:
            info = profiles.get(profile_name, {})
            state = info.get("state", "unknown")
            is_on = state.lower() == "on"
            if is_on:
                enabled_count += 1

            records.append(TelemetryRecord(
                collector=self.name,
                metric="firewall_profile_state",
                value=state,
                metadata={
                    "profile": profile_name,
                    "enabled": is_on,
                    "inbound_default": info.get("inbound"),
                    "outbound_default": info.get("outbound"),
                },
            ))

        records.append(TelemetryRecord(
            collector=self.name,
            metric="firewall_summary",
            value=enabled_count,
            metadata={
                "total_profiles": len(PROFILE_NAMES),
                "enabled_count": enabled_count,
            },
        ))
        return records

    # runs netsh advfirewall and returns the stdout, or None on failure
    def _run_netsh(self) -> str | None:
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles"],
                capture_output=True, text=True, timeout=8,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            self.log.warning("netsh advfirewall failed: %s", e)
            return None

    # parses netsh output into a per-profile dict of state/inbound/outbound.
    def _parse_profiles(self, output: str) -> dict[str, dict]:
        """Split output into profile sections, extract state + policies."""
        profiles: dict[str, dict] = {}
        current: str | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()

            # section header line, e.g. "Domain Profile Settings:"
            header_m = re.match(r"^(Domain|Private|Public)\s+Profile\s+Settings:", line)
            if header_m:
                current = header_m.group(1)
                profiles[current] = {}
                continue

            if current is None:
                continue

            state_m = re.match(r"^State\s+(\S+)", line)
            if state_m:
                profiles[current]["state"] = state_m.group(1)
                continue

            policy_m = re.match(r"^Firewall\s+Policy\s+(.+)$", line)
            if policy_m:
                policy = policy_m.group(1).strip()
                parts = [p.strip() for p in policy.split(",")]
                for p in parts:
                    lower = p.lower()
                    if "inbound" in lower:
                        profiles[current]["inbound"] = p
                    elif "outbound" in lower:
                        profiles[current]["outbound"] = p
                continue

        return profiles

    def _unavailable_record(self) -> TelemetryRecord:
        return TelemetryRecord(
            collector=self.name,
            metric="firewall_summary",
            value=None,
            metadata={
                "unavailable": True,
                "reason": "netsh advfirewall unavailable",
            },
        )


if __name__ == "__main__":
    # manual test: python -m src.collectors.firewall
    import json

    collector = FirewallCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))