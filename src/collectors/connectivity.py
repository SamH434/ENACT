"""
Connectivity collector pings configured targets and emits latency/loss.

On Windows, 'ping <host> -n <count>' runs 'count' echo requests, parse the
summary lines for sent/received/lost counts and the min/avg/max latency.
"""

import re
import subprocess

from src.collectors.base import Collector
from src.utils.records import TelemetryRecord

# defaults
DEFAULT_TARGETS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
DEFAULT_COUNT = 4              # number of pings per cycle per target
DEFAULT_TIMEOUT_MS = 1000      # per ping timeout

# Windows ping notes
# "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),"
# "Minimum = 8ms, Maximum = 14ms, Average = 11ms"
_LOSS_RE = re.compile(
    r"Sent = (\d+), Received = (\d+), Lost = (\d+)"
)
_TIMING_RE = re.compile(
    r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms"
)

class ConnectivityCollector(Collector):
    name = "connectivity"

    def __init__(
        self,
        targets: list[str] | None = None,
        count: int = DEFAULT_COUNT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        super().__init__()
        self.targets = targets or DEFAULT_TARGETS
        self.count = count
        self.timeout_ms = timeout_ms

    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        for target in self.targets:
            records.extend(self._ping_one(target))
        return records

    def _ping_one(self, target: str) -> list[TelemetryRecord]:
        """Run ping against a single target and produce records."""
        try:
            result = subprocess.run(
                ["ping", target, "-n", str(self.count), "-w", str(self.timeout_ms)],
                capture_output=True,
                text=True,
                timeout=self.count * (self.timeout_ms / 1000) + 5,
            )
        except subprocess.TimeoutExpired:
            self.log.warning("ping to %s timed out", target)
            return [self._unreachable_record(target, reason="timeout")]
        except FileNotFoundError:
            self.log.error("ping command not found on this system")
            return []

        output = result.stdout
        return self._parse_ping_output(target, output)

    def _parse_ping_output(self, target: str, output: str) -> list[TelemetryRecord]:
        loss_match = _LOSS_RE.search(output)
        timing_match = _TIMING_RE.search(output)

        if not loss_match:
            self.log.warning("could not parse ping output for %s", target)
            return [self._unreachable_record(target, reason="parse_failure")]

        sent = int(loss_match.group(1))
        received = int(loss_match.group(2))
        lost = int(loss_match.group(3))
        loss_pct = (lost / sent * 100) if sent else 100.0

        records: list[TelemetryRecord] = [
            TelemetryRecord(
                collector=self.name,
                metric="packet_loss_pct",
                value=round(loss_pct, 2),
                metadata={"target": target, "sent": sent, "received": received, "lost": lost},
            )
        ]

        if timing_match and received > 0:
            min_ms = int(timing_match.group(1))
            max_ms = int(timing_match.group(2))
            avg_ms = int(timing_match.group(3))
            jitter_ms = max_ms - min_ms  # crude but useful

            records.append(
                TelemetryRecord(
                    collector=self.name,
                    metric="latency_ms",
                    value=avg_ms,
                    metadata={
                        "target": target,
                        "min_ms": min_ms,
                        "max_ms": max_ms,
                        "jitter_ms": jitter_ms,
                    },
                )
            )

        return records

    def _unreachable_record(self, target: str, reason: str) -> TelemetryRecord:
        return TelemetryRecord(
            collector=self.name,
            metric="packet_loss_pct",
            value=100.0,
            metadata={"target": target, "reason": reason, "unreachable": True},
        )


if __name__ == "__main__":
    # manual testing: python -m src.collectors.connectivity
    import json

    collector = ConnectivityCollector()
    for record in collector.collect():
        print(json.dumps(record.to_dict(), indent=2))