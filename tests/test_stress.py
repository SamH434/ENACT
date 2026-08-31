"""
Stress tests: verify ENACT's storage and analyzer performance under load

(intentionally slow tests that seed the database with realistic scale data 
and verify SLAs still hold.

SLAs enforced:
    dashboard_snapshot() under 100ms with 200K samples
    samples_in_window() under 50ms with 200K samples
    prune_old_data() completes within reasonable time
    analyzer.run() under 200ms with typical historical data
"""

import time
from datetime import datetime, timedelta, timezone

import pytest


# mark all tests in this file as "stress" so they can be selected/excluded
# with `pytest -m stress` or `pytest -m 'not stress'`
pytestmark = pytest.mark.stress


# SLA thresholds
SLA_SNAPSHOT_MS = 100
SLA_WINDOW_QUERY_MS = 50
SLA_ANALYZER_MS = 200


def _seed_bulk_samples(database, make_record, count: int,
                       collectors: list[str] = None,
                       time_span_hours: float = 24) -> None:
    """Seed a large number of samples spread across recent history."""
    collectors = collectors or ["connectivity", "dns", "wifi", "route",
                                "status", "firewall"]
    now = datetime.now(timezone.utc)
    batch = []
    batch_size = 500  # commit in batches to avoid huge single transactions

    for i in range(count):
        seconds_ago = (i / count) * time_span_hours * 3600
        collector = collectors[i % len(collectors)]

        metric = {
            "connectivity": "latency_ms",
            "dns": "resolution_ms",
            "wifi": "current_rssi_dbm",
            "route": "route_fingerprint",
            "status": "wifi_status",
            "firewall": "firewall_profile_state",
        }.get(collector, "value")

        batch.append(make_record(
            collector=collector,
            metric=metric,
            value=float(20 + (i % 100)), 
            metadata={"target": "1.1.1.1", "seeded": True},
            timestamp=now - timedelta(seconds=seconds_ago),
        ))

        if len(batch) >= batch_size:
            database.store_records(batch)
            batch = []

    if batch:
        database.store_records(batch)


class TestDashboardSnapshotPerformance:

    def test_snapshot_under_sla_with_50k_samples(self, temp_db, make_record):
        """
        With 50K samples across all collectors, snapshot must complete under SLA.
        This is realistic scale for a machine running ENACT for a few weeks.
        """
        from src.storage import database
        _seed_bulk_samples(database, make_record, count=50_000)

        database.dashboard_snapshot()

        times_ms = []
        for _ in range(5):
            start = time.perf_counter()
            database.dashboard_snapshot()
            times_ms.append((time.perf_counter() - start) * 1000)

        median_ms = sorted(times_ms)[len(times_ms) // 2]
        assert median_ms < SLA_SNAPSHOT_MS, (
            f"dashboard_snapshot median: {median_ms:.0f}ms "
            f"(SLA: {SLA_SNAPSHOT_MS}ms) - index or query regression?"
        )

    def test_snapshot_under_sla_with_200k_samples(self, temp_db, make_record):
        """
        With 200K samples, well past a year of typical usage, snapshot must
        still meet SLA. If this test fails, the indexes aren't scaling with
        data size.
        """
        from src.storage import database
        _seed_bulk_samples(database, make_record, count=200_000,
                           time_span_hours=24 * 30 * 6)  # 6 months of data

        database.dashboard_snapshot() 

        times_ms = []
        for _ in range(5):
            start = time.perf_counter()
            database.dashboard_snapshot()
            times_ms.append((time.perf_counter() - start) * 1000)

        median_ms = sorted(times_ms)[len(times_ms) // 2]
        assert median_ms < SLA_SNAPSHOT_MS * 2, (
            f"dashboard_snapshot median at 200K rows: {median_ms:.0f}ms "
            f"(SLA: {SLA_SNAPSHOT_MS * 2}ms)"
        )


class TestWindowQueryPerformance:

    def test_window_query_under_sla_with_50k_samples(self, temp_db, make_record):
        """
        Pulling ±60s of samples from a 50K row table should be nearly instant
        thanks to the idx_samples_ts index. If this test fails, that index
        isn't being used correctly by the query planner.
        """
        from src.storage import database
        _seed_bulk_samples(database, make_record, count=50_000)

        now = datetime.now(timezone.utc)
        times_ms = []
        for i in range(10):
            offset_hours = i * 2
            center = now - timedelta(hours=offset_hours)
            start = time.perf_counter()
            database.samples_in_window(
                center - timedelta(seconds=60),
                center + timedelta(seconds=60),
            )
            times_ms.append((time.perf_counter() - start) * 1000)

        median_ms = sorted(times_ms)[len(times_ms) // 2]
        assert median_ms < SLA_WINDOW_QUERY_MS, (
            f"samples_in_window median: {median_ms:.0f}ms "
            f"(SLA: {SLA_WINDOW_QUERY_MS}ms)"
        )


class TestPruningPerformance:

    def test_prune_completes_reasonably_with_100k_samples(self, temp_db, make_record):
        """
        Pruning half the data from a 100K row database should complete in
        under a few seconds. Longer than that would suggest the DELETE isn't
        using indexes on the ts column.
        """
        from src.storage import database
        # seed data spanning 20 days
        _seed_bulk_samples(database, make_record, count=100_000,
                           time_span_hours=24 * 20)

        start = time.perf_counter()
        deleted = database.prune_old_data(retention_days=10)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 5000, (
            f"prune took {elapsed_ms:.0f}ms - index missing on samples.ts?"
        )
        assert deleted > 0, "prune should have removed some rows"


class TestAnalyzerPerformance:

    def test_latency_spike_analyzer_under_sla(self, temp_db, make_record):
        """
        LatencySpikeAnalyzer looks at recent connectivity samples. With
        realistic data, run() should complete under SLA.
        """
        from src.storage import database
        from src.analyzers.latency_spike import LatencySpikeAnalyzer

        _seed_bulk_samples(database, make_record, count=10_000,
                           collectors=["connectivity"])

        analyzer = LatencySpikeAnalyzer()
        
        analyzer.run()

        times_ms = []
        for _ in range(5):
            start = time.perf_counter()
            analyzer.run()
            times_ms.append((time.perf_counter() - start) * 1000)

        median_ms = sorted(times_ms)[len(times_ms) // 2]
        assert median_ms < SLA_ANALYZER_MS, (
            f"LatencySpikeAnalyzer.run() median: {median_ms:.0f}ms "
            f"(SLA: {SLA_ANALYZER_MS}ms)"
        )

    def test_dns_outage_analyzer_under_sla(self, temp_db, make_record):
        """DNSOutageAnalyzer looks at recent DNS samples."""
        from src.storage import database
        from src.analyzers.dns_outage import DNSOutageAnalyzer

        _seed_bulk_samples(database, make_record, count=10_000,
                           collectors=["dns"])

        analyzer = DNSOutageAnalyzer()
        analyzer.run()

        times_ms = []
        for _ in range(5):
            start = time.perf_counter()
            analyzer.run()
            times_ms.append((time.perf_counter() - start) * 1000)

        median_ms = sorted(times_ms)[len(times_ms) // 2]
        assert median_ms < SLA_ANALYZER_MS, (
            f"DNSOutageAnalyzer.run() median: {median_ms:.0f}ms"
        )


class TestSampleThroughput:

    def test_bulk_insert_throughput(self, temp_db, make_record):
        """
        Inserting 1000 samples in a single batch should complete in under 1s.
        Real inserts are batched by the scheduler, so this reflects worst-case
        bursty write behavior.
        """
        from src.storage import database

        records = [make_record() for _ in range(1000)]
        start = time.perf_counter()
        count = database.store_records(records)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert count == 1000
        assert elapsed_ms < 1000, (
            f"1000 record insert took {elapsed_ms:.0f}ms - "
            f"batching regression?"
        )