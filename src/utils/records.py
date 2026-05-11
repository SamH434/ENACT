"""
Shared data contracts for ENACT collectors.

Every collector emits TelemetryRecord instances. Keeping the shape uniform
across heterogeneous data sources is what makes the storage and analysis
layers simple later.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

"""
A single telemetry data point from any collector.
"""
@dataclass
class TelemetryRecord:
    collector: str  # e.g. "connectivity", "dns", "route", "wifi"
    metric: str     # e.g. "latency_ms", "packet_loss_pct", "rssi_dbm"

    """
    line below: flexible enough that route fingerprints (strings) and latencies (floats) 
    and counts (ints) all fit, while still being explicit about what's allowed.
    """
    value: float | int | str | None  # the measurement itself
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    # Reference
    # connectivity: {"target": "1.1.1.1", "min_ms": 8, "max_ms": 14}
    # dns:          {"hostname": "google.com", "resolver": "1.1.1.1"}
    # route:        {"target": "1.1.1.1", "hop_count": 7, "fingerprint": "..."}
    # wifi:         {"ssid": "MyWifi", "channel": 6, "bssid": "aa:bb:..."}

    # plain dict suitable for SQLite/JSON storage
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d