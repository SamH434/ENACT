"""
Base class for all ENACT analyzers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from src.utils.logger import get_logger


@dataclass
class Event:
    """A single detected anomaly with supporting cross-signal evidence."""
    type: str               # e.g. "latency_spike", "dns_outage", "route_change"
    severity: str           # "info" / "warning" / "critical"
    summary: str            # human-readable one-liner
    # evidence is the cross-signal proof: samples from other collectors in the
    # same time window as the anomaly. This is the OVER-ENACT idea made
    # concrete, an event isn't just a flag, it's a flag PLUS what everything
    # else was doing when it fired
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # serializes to a plain dict for the events table, timestamp as ISO string
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


"""
Abstract base for all ENACT analyzers.

Defines the run() contract every analyzer implements. Like Collector, this
keeps the scheduler logic uniform: it doesn't care WHAT an analyzer does,
only that it has a name and a run() method that returns a list of events.
"""
class Analyzer(ABC):

    # subclasses override this with their analyzer name (e.g. "latency_spike")
    name: str = "base"

    # sets up the per-analyzer logger when a subclass is instantiated
    def __init__(self) -> None:
        self.log = get_logger(f"enact.analyzers.{self.name}")

    # runs one analysis pass, returns any events detected since last run
    @abstractmethod
    def run(self) -> list[Event]:
        """Examine recent samples and return any events detected."""
        raise NotImplementedError