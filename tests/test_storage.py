"""
Storage layer tests: verify SQL helpers return expected rows.
"""

from datetime import datetime, timedelta, timezone


class TestStoreRecords:

    def test_store_records_inserts_all_and_returns_count(self, temp_db, make_record):
        """store_records returns the number of rows written."""
        from src.storage import database

        records = [make_record() for _ in range(5)]
        count = database.store_records(records)
        assert count == 5

    def test_store_records_handles_empty_list(self, temp_db):
        """Empty input returns 0, does not crash."""
        from src.storage import database

        count = database.store_records([])
        assert count == 0

    def test_numeric_value_goes_to_value_column(self, temp_db, make_record):
        """Float values are stored in the numeric value column."""
        from src.storage import database

        database.store_records([make_record(value=42.5)])
        rows = database.recent_samples("connectivity", limit=10)
        assert len(rows) == 1
        assert rows[0]["value"] == 42.5
        assert rows[0]["value_str"] is None

    def test_string_value_goes_to_value_str_column(self, temp_db, make_record):
        """String values are stored in value_str for route fingerprints, etc."""
        from src.storage import database

        database.store_records([make_record(
            collector="route", metric="route_fingerprint",
            value="abc123def456",
        )])
        rows = database.recent_samples("route", limit=10)
        assert len(rows) == 1
        assert rows[0]["value"] is None
        assert rows[0]["value_str"] == "abc123def456"


class TestRecentSamples:

    def test_recent_samples_filters_by_collector(self, temp_db, make_record):
        """recent_samples returns only rows for the requested collector."""
        from src.storage import database

        conn_records = [make_record(collector="connectivity") for _ in range(3)]
        dns_records = [make_record(collector="dns", metric="resolution_ms")
                       for _ in range(2)]
        database.store_records(conn_records + dns_records)

        assert len(database.recent_samples("connectivity", limit=100)) == 3
        assert len(database.recent_samples("dns", limit=100)) == 2

    def test_recent_samples_respects_limit(self, temp_db, make_record):
        """The limit parameter caps returned rows."""
        from src.storage import database

        database.store_records([make_record() for _ in range(20)])
        rows = database.recent_samples("connectivity", limit=5)
        assert len(rows) == 5


class TestSamplesInWindow:

    def test_returns_only_rows_within_window(self, temp_db, make_record):
        """
        samples_in_window is the correlation primitive. It must return exactly
        the rows whose timestamps fall between start and end (inclusive).
        """
        from src.storage import database

        now = datetime.now(timezone.utc)
        inside = [
            make_record(timestamp=now - timedelta(seconds=10)),
            make_record(timestamp=now),
            make_record(timestamp=now + timedelta(seconds=5)),
        ]
        outside = [
            make_record(timestamp=now - timedelta(minutes=30)),
            make_record(timestamp=now + timedelta(minutes=30)),
        ]
        database.store_records(inside + outside)

        rows = database.samples_in_window(
            start=now - timedelta(seconds=60),
            end=now + timedelta(seconds=60),
        )
        assert len(rows) == 3

    def test_returns_across_collectors(self, temp_db, make_record):
        from src.storage import database

        now = datetime.now(timezone.utc)
        mixed = [
            make_record(collector="connectivity", timestamp=now),
            make_record(collector="dns", timestamp=now),
            make_record(collector="wifi", timestamp=now),
        ]
        database.store_records(mixed)

        rows = database.samples_in_window(
            start=now - timedelta(seconds=30),
            end=now + timedelta(seconds=30),
        )
        collectors = {r["collector"] for r in rows}
        assert collectors == {"connectivity", "dns", "wifi"}


class TestDashboardHelpers:

    def test_latest_metric_snapshots_returns_newest_per_pair(self, temp_db,
                                                              make_record):
        """
        Two samples of the same (collector, metric) pair with different timestamps:
        only the newer one appears in the snapshot.
        """
        from src.storage import database

        now = datetime.now(timezone.utc)
        database.store_records([
            make_record(value=50.0, timestamp=now - timedelta(minutes=5)),
            make_record(value=25.0, timestamp=now),
        ])

        rows = database.latest_metric_snapshots()
        conn_rows = [r for r in rows
                     if r["collector"] == "connectivity"
                     and r["metric"] == "latency_ms"]
        assert len(conn_rows) == 1
        assert conn_rows[0]["value"] == 25.0

    def test_dashboard_snapshot_returns_all_sections(self, temp_db, make_record):
        """dashboard_snapshot returns the four keys the frontend expects."""
        from src.storage import database

        database.store_records([make_record() for _ in range(3)])
        snap = database.dashboard_snapshot()

        assert "collector_health" in snap
        assert "current_metrics" in snap
        assert "events" in snap
        assert "status" in snap


class TestEvents:

    def test_store_and_retrieve_event(self, temp_db):
        """store_event writes, event_by_id can read it back."""
        from src.storage import database

        now = datetime.now(timezone.utc)
        database.store_event(
            event_type="test_event",
            severity="warning",
            summary="test summary",
            evidence={"key": "value"},
            timestamp=now,
        )
        rows = database.recent_events(limit=10)
        assert len(rows) == 1
        assert rows[0]["type"] == "test_event"
        assert rows[0]["severity"] == "warning"

    def test_new_critical_events_since_watermark(self, temp_db):
        """
        new_critical_events_since returns only critical events with id > watermark.
        Used by the alarm watcher to detect events it hasn't yet shown.
        """
        from src.storage import database

        now = datetime.now(timezone.utc)
        database.store_event("warn", "warning", "w", {}, now)
        database.store_event("crit1", "critical", "c1", {}, now)
        database.store_event("crit2", "critical", "c2", {}, now)

        new = database.new_critical_events_since(-1)
        assert len(new) == 2
        assert {e["type"] for e in new} == {"crit1", "crit2"}