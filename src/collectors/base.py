"""
Base class for all ENACT collectors.

Every collector subclasses Collector and implements collect(), returning a
list of TelemetryRecord.
"""

from abc import ABC, abstractmethod

from src.utils.logger import get_logger
from src.utils.records import TelemetryRecord

"""
Abstract base for all telemetry collectors
"""
class Collector(ABC):

    name: str = "base" 

    def __init__(self) -> None:
        # makes log put easier to filter later
        self.log = get_logger(f"enact.collectors.{self.name}")

    # collect() will always returns a list, this makes scheduler logic uniform
    @abstractmethod # for enforcing correctness
    def collect(self) -> list[TelemetryRecord]:
        """Run one collection cycle and return zero or more records."""
        raise NotImplementedError