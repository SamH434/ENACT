"""
Base class for all ENACT collectors.

Every collector subclasses Collector and implements collect(), returning a
list of TelemetryRecord.
"""

from abc import ABC, abstractmethod

from src.utils.logger import get_logger
from src.utils.records import TelemetryRecord

"""
Abstract base for all telemetry collectors.

Defines the shared interface every collector must implement and gives each
subclass its own named logger automatically. The point is consistency: every
collector looks and behaves the same from the outside, so the scheduler and
storage layer in Phase 3 can treat them uniformly.
"""
class Collector(ABC):

    # subclasses override this with their own name (e.g. "connectivity", "dns")
    name: str = "base" 

    # sets up the per-collector logger when a subclass is instantiated
    def __init__(self) -> None:
        # makes log put easier to filter later
        self.log = get_logger(f"enact.collectors.{self.name}")

    # runs one collection cycle, returns the resulting telemetry records
    # collect() will always returns a list, this makes scheduler logic uniform
    @abstractmethod # for enforcing correctness
    def collect(self) -> list[TelemetryRecord]:
        """Run one collection cycle and return zero or more records."""
        raise NotImplementedError