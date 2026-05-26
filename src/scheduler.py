"""
Scheduler: runs each collector on its own interval in a background thread.

This is what turns ENACT from "four scripts you run by hand" into a system
that runs itself. Each collector gets its own thread and its own cadence,
because the collectors have wildly different costs: a DNS lookup is
milliseconds, a tracert can take a minute. Forcing them onto one shared clock
would mean the slow ones bottleneck the fast ones.

Each cycle:
    1. generate a fresh run_id for this collector pass
    2. call collector.collect()
    3. stamp every returned record with the run_id
    4. store the records and log the run's timing/health
    5. sleep until the next interval

Correlation across collectors is NOT done here. The collectors run
independently; correlation happens later in analysis by bucketing records
into time windows (see records.py). The scheduler's job is just to keep
everything running and precisely timestamped.
"""

import threading
import time
from datetime import datetime, timezone

from src.collectors.base import Collector
from src.storage import database
from src.utils.logger import get_logger
from src.utils.records import new_run_id

log = get_logger("enact.scheduler")


"""
Runs a single collector on a fixed interval in its own thread.

One CollectorWorker wraps one collector. The scheduler creates several of
these (one per collector) and starts them all. Each worker loops forever:
collect, store, sleep, repeat, until told to stop.
"""
class CollectorWorker:

    # binds a collector to its interval and sets up the thread + stop signal
    def __init__(self, collector: Collector, interval_sec: float) -> None:
        self.collector = collector
        self.interval_sec = interval_sec
        self.log = get_logger(f"enact.scheduler.{collector.name}")
        # Event is a thread-safe flag. set() it to tell the loop to stop.
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

    # the forever-loop: collect, store, log, sleep, until stopped
    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            # wait() returns early if stop is set during the sleep, so shutdown
            # is responsive instead of having to wait out a full interval
            self._stop.wait(self.interval_sec)

    # runs one collection cycle and persists the results, never lets an
    # exception kill the thread (a crashing collector shouldn't take down ENACT)
    def _run_once(self) -> None:
        run_id = new_run_id()
        start = time.perf_counter()
        try:
            records = self.collector.collect()
            # stamp every record from this cycle with the shared run_id.
            # the collectors don't know about run_ids, we inject it here, which
            # is why none of the collector files had to change for this phase
            for r in records:
                r.run_id = run_id
            count = database.store_records(records)
            duration_ms = (time.perf_counter() - start) * 1000
            database.store_run(run_id, self.collector.name, duration_ms, "ok", count)
            self.log.info("cycle ok: %d samples in %.0fms", count, duration_ms)
        except Exception as e:
            # broad except on purpose: a background collector must never crash
            # the whole program. log it, record the failed run, move on
            duration_ms = (time.perf_counter() - start) * 1000
            self.log.error("cycle failed: %s", e)
            database.store_run(run_id, self.collector.name, duration_ms, "error", 0)


"""
Owns the full set of collector workers and starts/stops them together.

This is the top-level orchestrator. main.py builds one Scheduler, hands it the
collectors and their intervals, and calls run_forever(). It also handles
periodic retention pruning so the database doesn't grow without bound.
"""
class Scheduler:

    # sets up the worker list and remembers the retention policy
    def __init__(self, retention_days: int = 7,
                 prune_interval_sec: float = 3600) -> None:
        self.workers: list[CollectorWorker] = []
        self.retention_days = retention_days
        self.prune_interval_sec = prune_interval_sec
        self._stop = threading.Event()

    # registers one collector to run at the given interval
    def add(self, collector: Collector, interval_sec: float) -> None:
        self.workers.append(CollectorWorker(collector, interval_sec))

    # starts every worker, then blocks running periodic pruning until interrupted
    def run_forever(self) -> None:
        database.init_db()
        log.info("starting %d collectors", len(self.workers))
        for w in self.workers:
            w.start()

        # the main thread stays alive here, periodically pruning old data.
        # Ctrl+C raises KeyboardInterrupt, which we catch for a clean shutdown
        try:
            while not self._stop.is_set():
                self._stop.wait(self.prune_interval_sec)
                if not self._stop.is_set():
                    database.prune_old_data(self.retention_days)
        except KeyboardInterrupt:
            log.info("shutdown requested")
        finally:
            self.stop()

    # stops every worker, used during shutdown
    def stop(self) -> None:
        self._stop.set()
        for w in self.workers:
            w.stop()
        log.info("all collectors stopped")