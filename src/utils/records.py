"""
Shared data contracts for ENACT collectors.

Every collector emits TelemetryRecord instances. Keeping the shape uniform
across heterogeneous data sources is what makes the storage and analysis
layers simple later.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import uuid

# generates a short unique id for a single collection cycle
def new_run_id() -> str:
    # short unique id stamped on every record from one collector cycle
    return uuid.uuid4().hex[:12]

"""
A single telemetry data point from any collector.
"""
@dataclass
class TelemetryRecord:
    collector: str  # e.g. "connectivity", "dns", "route", "wifi"
    metric: str     # e.g. "latency_ms", "packet_loss_pct", "rssi_dbm"
    value: float | int | str | None  # the measurement itself

    # run_id ties together every record from the same collector cycle.
    # the scheduler generates one per cycle and passes it in; correlation
    # across collectors is then done by time-window bucketing in analysis.
    run_id: str | None = None

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    # Reference
    # connectivity: {"target": "1.1.1.1", "min_ms": 8, "max_ms": 14}
    # dns:          {"hostname": "google.com", "resolver": "1.1.1.1"}
    # route:        {"target": "1.1.1.1", "hop_count": 7, "fingerprint": "..."}
    # wifi:         {"ssid": "MyWifi", "channel": 6, "bssid": "aa:bb:..."}

   # serializes to a plain dict for SQLite/JSON storage, timestamp as ISO string
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d