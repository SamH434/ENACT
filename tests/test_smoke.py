"""
Smoke tests: every module imports cleanly, all public classes construct
"""

import importlib
import pytest


ALL_MODULES = [
    "src.utils.logger",
    "src.utils.records",
    "src.storage.database",
    "src.collectors.base",
    "src.collectors.connectivity",
    "src.collectors.dns",
    "src.collectors.route",
    "src.collectors.wifi",
    "src.collectors.status",
    "src.analyzers.base",
    "src.analyzers.latency_spike",
    "src.analyzers.dns_outage",
    "src.analyzers.route_change",
    "src.analyzers.wifi_degradation",
    "src.scheduler",
    "src.collectors.firewall",
    "src.analyzers.firewall_disabled",
    "src.analyzers.rogue_ap",
]


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports(module_name):
    """Every ENACT module imports without error."""
    importlib.import_module(module_name)


def test_creating_many_loggers_does_not_crash():
    """
    The exact scenario of the historic logger bug: create many named loggers,
    write to all of them, verify no exception. With the shared-handler design
    only one file descriptor exists so rotation stays safe.
    """
    from src.utils.logger import get_logger

    loggers = [get_logger(f"enact.smoketest.mod{i}") for i in range(20)]
    for lg in loggers:
        lg.info("smoke test log line %s", lg.name)
    # if we reach here without exception, the shared-handler pattern is working


def test_all_collectors_instantiable():
    """Each Collector subclass constructs without touching the network."""
    from src.collectors.connectivity import ConnectivityCollector
    from src.collectors.dns import DNSCollector
    from src.collectors.route import RouteCollector
    from src.collectors.wifi import WifiCollector
    from src.collectors.status import StatusCollector
    from src.collectors.firewall import FirewallCollector
    from src.analyzers.rogue_ap import RogueAPAnalyzer

    for cls in [ConnectivityCollector, DNSCollector, RouteCollector,
                WifiCollector, StatusCollector, FirewallCollector, RogueAPAnalyzer]:
        instance = cls()
        assert instance.name, f"{cls.__name__} has no name attribute"


def test_all_analyzers_instantiable():
    """Each Analyzer subclass constructs cleanly."""
    from src.analyzers.latency_spike import LatencySpikeAnalyzer
    from src.analyzers.dns_outage import DNSOutageAnalyzer
    from src.analyzers.route_change import RouteChangeAnalyzer
    from src.analyzers.wifi_degradation import WifiDegradationAnalyzer
    from src.analyzers.firewall_disabled import FirewallDisabledAnalyzer

    for cls in [LatencySpikeAnalyzer, DNSOutageAnalyzer,
                RouteChangeAnalyzer, WifiDegradationAnalyzer, FirewallDisabledAnalyzer]:
        instance = cls()
        assert instance.name, f"{cls.__name__} has no name attribute"


def test_event_dataclass_shape():
    """The Event contract is stable: fields exist and defaults work."""
    from src.analyzers.base import Event

    e = Event(
        type="test",
        severity="warning",
        summary="test summary",
    )
    assert e.type == "test"
    assert e.severity == "warning"
    assert e.evidence == {}
    assert e.timestamp is not None
    d = e.to_dict()
    assert d["type"] == "test"


def test_telemetry_record_shape():
    """TelemetryRecord contract is stable: fields exist, to_dict works."""
    from src.utils.records import TelemetryRecord, new_run_id

    r = TelemetryRecord(
        collector="test",
        metric="dummy",
        value=42.0,
        run_id=new_run_id(),
    )
    assert r.collector == "test"
    assert r.value == 42.0
    assert r.timestamp is not None
    d = r.to_dict()
    assert "collector" in d and "metric" in d and "value" in d
    assert isinstance(d["timestamp"], str)  # ISO string for JSON