"""
Connectivity collector: pings configured targets and emits latency/loss.

Three failure modes are handled separately so a bad network never crashes
the collector:
    timeout:        ping hung past our deadline, emit 100% loss record
    file not found: ping isn't installed (shouldn't happen on Windows), log and skip
    parse failure:  output didn't match expected shape, emit 100% loss record
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
# we only parse the summary block, not the per-packet "Reply from..." lines.
# less parsing surface = fewer bugs
_LOSS_RE = re.compile(
    r"Sent = (\d+), Received = (\d+), Lost = (\d+)"
)
_TIMING_RE = re.compile(
    r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms"
)

"""
Pings a list of public targets each cycle and emits latency + packet loss records.

One subprocess call to 'ping' per target, parsed for the summary block.
Defaults to three well-known public DNS resolvers (Cloudflare, Google, Quad9)
since they're reliable, low-latency, and exist specifically to be reachable.
"""
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

    # runs one ping cycle across every configured target
    def collect(self) -> list[TelemetryRecord]:
        records: list[TelemetryRecord] = []
        for target in self.targets:
            records.extend(self._ping_one(target))
        return records

    # pings a single target, returns 1-2 records (loss always, latency if successful)
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

    # turns raw ping stdout into structured records, handles unparseable output gracefully
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

        # latency record only makes sense if at least one packet got through
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

    # builds a 100%-loss record for cases where the target couldn't be reached at all
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