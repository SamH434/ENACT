"""
Route collector - runs tracert against a target and produces a route fingerprint.

The fingerprint is a hash of the sequence of intermediate hop IPs (don't
care about the IPs themselves for anomaly detection) and whether or not the
sequence changed since the last run. 

Two consecutive collections with the same fingerprint = stable route
Different fingerprints = the path changed

Preserve timeouts in the fingerprint as a literal '*' so that "same route with
the same timeouts" is treated as stable, but "timeout where there used to
be a hop" registers as a change.
"""

import hashlib
import re
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord


DEFAULT_TARGETS = ["1.1.1.1", "8.8.8.8"]
DEFAULT_MAX_HOPS = 20
DEFAULT_TIMEOUT_MS = 2000 

# Windows tracert output we need to parse. Hop lines look something like:
#   "  1    <1 ms    <1 ms    <1 ms  192.168.1.1"
#   "  2     8 ms    9 ms    8 ms  10.0.0.1"
#   "  3     *        *        *     Request timed out."
# We capture the hop number and the final token (IP or '*').
_HOP_RE = re.compile(
    # regex reference:
    #================================================
    #   ^\s*(\d+)                                    - start of line, optional whitespace, then the hop number (captured but unused here).
    #   \s+.*?                                       - whitespace and then any characters, lazy, to skip the three probe times.
    #   (?:(\d+\.\d+\.\d+\.\d+)|Request timed out\.) - either an IPv4 address (captured) or the 
        #   literal "Request timed out." string. The (?:...) is a non-capturing group used purely for the OR.
    #   \s*$                                         - optional trailing whitespace, end of line.
    #   re.MULTILINE                                 - makes ^ and $ match at line boundaries, not just the start/end of the full output.
    r"^\s*(\d+)\s+.*?(?:(\d+\.\d+\.\d+\.\d+)|Request timed out\.)\s*$",
    re.MULTILINE,
)


class RouteCollector(Collector):
    name = "route"

    def __init__(
        self,
        targets: list[str] | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        super().__init__()
        self.targets = targets or DEFAULT_TARGETS
        self.max_hops = max_hops
        self.timeout_ms = timeout_ms

    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        for target in self.targets:
            record = self._trace_one(target)
            if record is not None:
                records.append(record)
        return records

    def _trace_one(self, target: str) -> TelemetryRecord | None:
        """Run tracert against one target and produce a route fingerprint record."""
        # tracert flags:
        # -d         don't resolve hop IPs to hostnames (faster, cleaner output)
        # -h <n>     max hops
        # -w <ms>    per-hop timeout
        # Total worst case time: max_hops * 3 * timeout_ms (with safety margin)
        worst_case_sec = (self.max_hops * 3 * self.timeout_ms / 1000) + 10

        try:
            result = subprocess.run(
                ["tracert", "-d", "-h", str(self.max_hops),
                 "-w", str(self.timeout_ms), target],
                capture_output=True,
                text=True,
                timeout=worst_case_sec,
            )
        except subprocess.TimeoutExpired:
            self.log.warning("tracert to %s exceeded overall timeout", target)
            return None
        except FileNotFoundError:
            self.log.error("tracert command not found on this system")
            return None

        hops = self._parse_hops(result.stdout)

        if not hops:
            self.log.warning("no hops parsed from tracert output to %s", target)
            return None

        fingerprint = self._fingerprint(hops)

        return TelemetryRecord(
            collector=self.name,
            metric="route_fingerprint",
            value=fingerprint,
            metadata={
                "target": target,
                "hop_count": len(hops),
                "hops": hops,
                "timed_out_hops": sum(1 for h in hops if h == "*"),
            },
        )

    def _parse_hops(self, output: str) -> list[str]:
        """Extract the ordered list of hop IPs (or '*' for timeouts)."""
        hops: list[str] = []
        for match in _HOP_RE.finditer(output):
            ip = match.group(2)
            hops.append(ip if ip else "*")
        return hops

    def _fingerprint(self, hops: list[str]) -> str:
        """Stable hash of the hop sequence. First 12 hex chars is plenty."""
        joined = "|".join(hops)
        return hashlib.sha256(joined.encode()).hexdigest()[:12]


if __name__ == "__main__":
    import json

    collector = RouteCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))