"""
Scheduler: runs each collector on its own interval in a background thread
to prevent bottlenecks. Each collector is responsible for its own timing and health.

Each cycle:
    1. generate a fresh run_id for this collector pass
    2. call collector.collect()
    3. stamp every returned record with the run_id
    4. store the records and log the run's timing/health
    5. sleep until the next interval

The collectors run independently; correlation happens later in analysis 
by bucketing records into time windows (see records.py). The scheduler's 
job is just to keep everything running and precisely timestamped.
"""

import threading
import time
from datetime import datetime, timezone

from src.analyzers.base import Analyzer
from src.collectors.base import Collector
from src.storage import database
from src.utils.logger import get_logger
from src.utils.records import new_run_id

log = get_logger("enact.scheduler")


"""
One CollectorWorker wraps one collector. The scheduler creates several of
these (one per collector) and starts them all. Each worker loops forever:
collect, store, sleep, repeat, until told to stop.
"""
class CollectorWorker:

    def __init__(self, collector: Collector, interval_sec: float) -> None:
        self.collector = collector
        self.interval_sec = interval_sec
        self.log = get_logger(f"enact.scheduler.{collector.name}")
        self._stop = threading.Event()
        # daemon=True means the thread won't block the program from exiting
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    # starts the worker's background thread
    def start(self) -> None:
        self.log.info("starting (interval %.0fs)", self.interval_sec)
        self._thread.start()

    # signals the worker loop to stop after its current cycle
    def stop(self) -> None:
        self._stop.set()

    # IMPORTANT: the forever-loop: collect, store, log, sleep, until stopped
    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            self._stop.wait(self.interval_sec)

    # runs one collection cycle and persists the results, never lets an
    # exception kill the thread (so a crashing collector won't take down the whole program)
    def _run_once(self) -> None:
        run_id = new_run_id()
        start = time.perf_counter()
        try:
            records = self.collector.collect()
            for r in records:
                r.run_id = run_id
            count = database.store_records(records)
            duration_ms = (time.perf_counter() - start) * 1000
            database.store_run(run_id, self.collector.name, duration_ms, "ok", count)
            self.log.info("cycle ok: %d samples in %.0fms", count, duration_ms)
        except Exception as e:
            # broad except on purpose: a background collector must never crash
            # the whole program. logging and storing the error is enough.
            duration_ms = (time.perf_counter() - start) * 1000
            self.log.error("cycle failed: %s", e)
            database.store_run(run_id, self.collector.name, duration_ms, "error", 0)

"""
Mirrors CollectorWorker but for analyzers. The cycle is: call analyzer.run(),
store any returned events to the database, log the cycle. No run_id stamping
because events already carry their own timestamp and severity.
"""
class AnalyzerWorker:

    def __init__(self, analyzer: Analyzer, interval_sec: float) -> None:
        self.analyzer = analyzer
        self.interval_sec = interval_sec
        self.log = get_logger(f"enact.scheduler.{analyzer.name}")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    # starts the worker's background thread
    def start(self) -> None:
        self.log.info("starting analyzer (interval %.0fs)", self.interval_sec)
        self._thread.start()

    # signals the loop to stop after the current cycle
    def stop(self) -> None:
        self._stop.set()

    # the forever-loop: run, store events, sleep, repeat, until stopped
    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            self._stop.wait(self.interval_sec)

    # runs one analysis cycle and stores any events, never lets exceptions
    # kill the thread (same with collector bug)
    def _run_once(self) -> None:
        start = time.perf_counter()
        try:
            events = self.analyzer.run()
            for e in events:
                database.store_event(
                    e.type, e.severity, e.summary, e.evidence, e.timestamp
                )
            duration_ms = (time.perf_counter() - start) * 1000
            if events:
                self.log.info("cycle ok: %d events in %.0fms",
                              len(events), duration_ms)
            else:
                self.log.debug("cycle ok: 0 events in %.0fms", duration_ms)
        except Exception as e:
            self.log.error("cycle failed: %s", e)

"""
Owns the full set of collector workers and starts/stops them together.

main.py builds one Scheduler, hands it the collectors and their intervals, 
and calls run_forever(). It also handles periodic retention pruning so the 
database doesn't grow without bound.
"""
class Scheduler:

    def __init__(self, retention_days: int = 7,
                 prune_interval_sec: float = 3600) -> None:
        self.workers: list[CollectorWorker] = []
        self.analyzer_workers: list[AnalyzerWorker] = []
        self.retention_days = retention_days
        self.prune_interval_sec = prune_interval_sec
        self._stop = threading.Event()

    # registers one collector to run at the given interval
    def add(self, collector: Collector, interval_sec: float) -> None:
        self.workers.append(CollectorWorker(collector, interval_sec))

    # registers one analyzer to run at the given interval
    def add_analyzer(self, analyzer: Analyzer, interval_sec: float) -> None:
        self.analyzer_workers.append(AnalyzerWorker(analyzer, interval_sec))

    # starts every worker, then keeps the main thread alive in short, interruptible
    # slices so Ctrl+C is responsive (long blocking waits swallow the signal on Windows)
    def run_forever(self) -> None:
        database.init_db()
        log.info("starting %d collectors and %d analyzers",
                 len(self.workers), len(self.analyzer_workers))
        for w in self.workers:
            w.start()
        for a in self.analyzer_workers:
            a.start()

        seconds_until_prune = self.prune_interval_sec
        try:
            while not self._stop.is_set():
                # time.sleep(1) is reliably interrupted by Ctrl+C
                time.sleep(1)
                seconds_until_prune -= 1
                if seconds_until_prune <= 0:
                    database.prune_old_data(self.retention_days)
                    seconds_until_prune = self.prune_interval_sec
        except KeyboardInterrupt:
            log.info("shutdown requested")
        finally:
            self.stop()

    # stops every worker (collectors and analyzers), used during shutdown
    def stop(self) -> None:
        self._stop.set()
        for w in self.workers:
            w.stop()
        for a in self.analyzer_workers:
            a.stop()
        log.info("all workers stopped")