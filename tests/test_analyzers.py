"""
Analyzer tests: verify severity thresholds fire (or don't) as documented in
OPERATIONS.md
"""

from datetime import datetime, timedelta, timezone


class TestLatencySpikeAnalyzer:
    """
    Fires warning when current > 3x baseline AND current > 50ms floor.
    """

    def test_no_event_when_latency_stable(self, temp_db, make_record):
        """A stable stream of 20ms samples should never fire."""
        from src.storage import database
        from src.analyzers.latency_spike import LatencySpikeAnalyzer

        now = datetime.now(timezone.utc)
        # 15 samples all at 20ms, all for the same target
        records = [
            make_record(
                collector="connectivity", metric="latency_ms",
                value=20.0, metadata={"target": "1.1.1.1"},
                timestamp=now - timedelta(seconds=30 * i),
            )
            for i in range(15)
        ]
        database.store_records(records)

        events = LatencySpikeAnalyzer().run()
        assert events == []

    def test_no_event_below_absolute_floor(self, temp_db, make_record):
        """
        Even a 10x spike from 2ms to 20ms should not fire, because 20ms is
        below the 50ms floor. Small-number noise filter.
        """
        from src.storage import database
        from src.analyzers.latency_spike import LatencySpikeAnalyzer

        now = datetime.now(timezone.utc)
        # 8 old samples at 2ms baseline (older = higher i so newer come out first)
        baseline = [
            make_record(
                collector="connectivity", metric="latency_ms",
                value=2.0, metadata={"target": "1.1.1.1"},
                timestamp=now - timedelta(seconds=30 * (i + 1)),
            )
            for i in range(8)
        ]
        # newest sample "spikes" to 20ms (10x baseline but under 50ms floor)
        current = make_record(
            collector="connectivity", metric="latency_ms",
            value=20.0, metadata={"target": "1.1.1.1"},
            timestamp=now,
        )
        database.store_records(baseline + [current])

        events = LatencySpikeAnalyzer().run()
        assert events == [], "spike below absolute floor should not fire"

    def test_fires_when_multiplier_and_floor_both_exceeded(self, temp_db, make_record):
        """
        150ms current + 25ms baseline: 6x multiplier, above 50ms floor -> fire warning.
        """
        from src.storage import database
        from src.analyzers.latency_spike import LatencySpikeAnalyzer

        now = datetime.now(timezone.utc)
        baseline = [
            make_record(
                collector="connectivity", metric="latency_ms",
                value=25.0, metadata={"target": "1.1.1.1"},
                timestamp=now - timedelta(seconds=30 * (i + 1)),
            )
            for i in range(10)
        ]
        current = make_record(
            collector="connectivity", metric="latency_ms",
            value=150.0, metadata={"target": "1.1.1.1"},
            timestamp=now,
        )
        database.store_records(baseline + [current])

        events = LatencySpikeAnalyzer().run()
        assert len(events) == 1
        assert events[0].severity == "warning"
        assert events[0].type == "latency_spike"


class TestDNSOutageAnalyzer:
    """
    Fires warning at 50% failure rate, critical at 90% over 40-sample window.
    """

    def _seed_dns(self, database, make_record, total: int, failures: int):
        """Helper: seed a mix of successful and failed DNS records."""
        now = datetime.now(timezone.utc)
        records = []
        for i in range(total):
            success = i >= failures
            records.append(make_record(
                collector="dns",
                metric="resolution_ms",
                value=25.0 if success else None,
                metadata={
                    "hostname": "test.example",
                    "success": success,
                },
                timestamp=now - timedelta(seconds=60 - i),
            ))
        database.store_records(records)

    def test_no_event_at_low_failure_rate(self, temp_db, make_record):
        """20% failure rate is below the warning threshold."""
        from src.storage import database
        from src.analyzers.dns_outage import DNSOutageAnalyzer

        self._seed_dns(database, make_record, total=40, failures=8)
        events = DNSOutageAnalyzer().run()
        assert events == []

    def test_fires_warning_at_50_percent(self, temp_db, make_record):
        """50% failure rate crosses the warning threshold exactly."""
        from src.storage import database
        from src.analyzers.dns_outage import DNSOutageAnalyzer

        self._seed_dns(database, make_record, total=40, failures=22)
        events = DNSOutageAnalyzer().run()
        assert len(events) == 1
        assert events[0].severity == "warning"

    def test_fires_critical_at_90_percent(self, temp_db, make_record):
        """90%+ failure rate is a critical event."""
        from src.storage import database
        from src.analyzers.dns_outage import DNSOutageAnalyzer

        self._seed_dns(database, make_record, total=40, failures=38)
        events = DNSOutageAnalyzer().run()
        assert len(events) == 1
        assert events[0].severity == "critical"


class TestRouteChangeAnalyzer:
    """
    Fires info-severity when the newest route fingerprint differs from the
    previous one for the same target.
    """

    def test_no_event_when_fingerprint_stable(self, temp_db, make_record):
        """Same fingerprint over time -> no event."""
        from src.storage import database
        from src.analyzers.route_change import RouteChangeAnalyzer

        now = datetime.now(timezone.utc)
        records = [
            make_record(
                collector="route", metric="route_fingerprint",
                value="stablefp001",
                metadata={"target": "1.1.1.1", "hop_count": 12},
                timestamp=now - timedelta(seconds=300 * i),
            )
            for i in range(5)
        ]
        database.store_records(records)

        events = RouteChangeAnalyzer().run()
        assert events == []

    def test_fires_info_when_fingerprint_changes(self, temp_db, make_record):
        """A different fingerprint for the same target fires an info event."""
        from src.storage import database
        from src.analyzers.route_change import RouteChangeAnalyzer

        now = datetime.now(timezone.utc)
        old = make_record(
            collector="route", metric="route_fingerprint",
            value="oldfp0000000",
            metadata={"target": "1.1.1.1", "hop_count": 12},
            timestamp=now - timedelta(seconds=600),
        )
        new = make_record(
            collector="route", metric="route_fingerprint",
            value="newfp1111111",
            metadata={"target": "1.1.1.1", "hop_count": 13},
            timestamp=now,
        )
        database.store_records([old, new])

        events = RouteChangeAnalyzer().run()
        assert len(events) == 1
        assert events[0].severity == "info"
        assert events[0].type == "route_change"


class TestWifiDegradationAnalyzer:
    """
    Fires warning when RSSI drops >= 15 dB below baseline AND is below -70 dBm.
    """

    def test_no_event_when_signal_stable(self, temp_db, make_record):
        """Stable RSSI at -55 dBm should not fire."""
        from src.storage import database
        from src.analyzers.wifi_degradation import WifiDegradationAnalyzer

        now = datetime.now(timezone.utc)
        records = [
            make_record(
                collector="wifi", metric="current_rssi_dbm",
                value=-55.0,
                metadata={"ssid": "TestNet", "bssid": "aa:bb:cc:dd:ee:01",
                          "channel": 36},
                timestamp=now - timedelta(seconds=120 * i),
            )
            for i in range(8)
        ]
        database.store_records(records)

        events = WifiDegradationAnalyzer().run()
        assert events == []

    def test_no_event_when_drop_below_concerning_floor(self, temp_db, make_record):
        """
        A drop from -50 to -68 is 18 dB (over the 15 dB threshold) but the
        current value is still above the -70 dBm concerning floor, so no event.
        """
        from src.storage import database
        from src.analyzers.wifi_degradation import WifiDegradationAnalyzer

        now = datetime.now(timezone.utc)
        baseline = [
            make_record(
                collector="wifi", metric="current_rssi_dbm",
                value=-50.0,
                metadata={"ssid": "TestNet", "bssid": "aa:bb:cc:dd:ee:01"},
                timestamp=now - timedelta(seconds=120 * (i + 1)),
            )
            for i in range(7)
        ]
        current = make_record(
            collector="wifi", metric="current_rssi_dbm",
            value=-68.0,  # 18 dB drop but still above -70 floor
            metadata={"ssid": "TestNet", "bssid": "aa:bb:cc:dd:ee:01"},
            timestamp=now,
        )
        database.store_records(baseline + [current])

        events = WifiDegradationAnalyzer().run()
        assert events == []

    def test_fires_warning_when_dropped_and_below_floor(self, temp_db, make_record):
        """
        Drop of 25 dB (from -55 to -80), and -80 is well below -70 floor.
        Both conditions met, should fire warning.
        """
        from src.storage import database
        from src.analyzers.wifi_degradation import WifiDegradationAnalyzer

        now = datetime.now(timezone.utc)
        baseline = [
            make_record(
                collector="wifi", metric="current_rssi_dbm",
                value=-55.0,
                metadata={"ssid": "TestNet", "bssid": "aa:bb:cc:dd:ee:01"},
                timestamp=now - timedelta(seconds=120 * (i + 1)),
            )
            for i in range(7)
        ]
        current = make_record(
            collector="wifi", metric="current_rssi_dbm",
            value=-80.0,
            metadata={"ssid": "TestNet", "bssid": "aa:bb:cc:dd:ee:01"},
            timestamp=now,
        )
        database.store_records(baseline + [current])

        events = WifiDegradationAnalyzer().run()
        assert len(events) == 1
        assert events[0].severity == "warning"
        assert events[0].type == "wifi_degradation"