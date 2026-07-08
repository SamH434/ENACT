"""
SQLite storage layer for ENACT

Owns the database schema and is the ONLY place in the codebase that touches
SQL. Collectors and the scheduler call store_records() and the query helpers;
they never write SQL themselves. This keeps the rest of the code clean and
means a future change (different DB, new column) only touches this file.

Three tables:

    samples: raw telemetry, one row per metric reading. the bulk of the data.
    events:  detected anomalies / correlated incidents (populated in Phase 4).
    runs:    one row per collector cycle, for timing and health tracking.

The samples table carries run_id and a precise timestamp, which together are
what ENACT's correlation model relies on
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from src.utils.logger import get_logger
from src.utils.records import TelemetryRecord

log = get_logger("enact.storage")

# database lives in data/ at the project root
DB_PATH = Path(__file__).resolve().parents[2] / "data" / "enact.db"

# schema as plain SQL. executed once on startup; "IF NOT EXISTS" makes it idempotent
_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,          -- ISO 8601 UTC timestamp
    run_id      TEXT,                      -- ties records from one collector cycle together
    collector   TEXT    NOT NULL,          -- connectivity / dns / route / wifi
    metric      TEXT    NOT NULL,          -- latency_ms / packet_loss_pct / etc.
    value       REAL,                      -- numeric reading (NULL for failures or string metrics)
    value_str   TEXT,                      -- string reading (e.g. route fingerprints)
    meta_json   TEXT                       -- per-collector metadata as JSON
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    type        TEXT    NOT NULL,          -- e.g. "latency_spike", "dns_outage"
    severity    TEXT    NOT NULL,          -- "info" / "warning" / "critical"
    summary     TEXT    NOT NULL,          -- human-readable one-liner
    evidence_json TEXT                     -- supporting samples, the cross-signal proof (Phase 4)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    run_id      TEXT    NOT NULL,
    collector   TEXT    NOT NULL,
    duration_ms REAL,
    status      TEXT    NOT NULL,          -- "ok" / "error"
    sample_count INTEGER                   -- how many samples this cycle produced
);

-- indexes for the queries the dashboard and analyzers will run most:
-- "give me recent samples for collector X" and "give me samples in a time window"
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS idx_samples_collector_ts ON samples(collector, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


# initializes the database file and creates tables if they don't exist yet
def init_db() -> None:
    """Create the database file and schema. Safe to call on every startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    log.info("database initialized at %s", DB_PATH)


# opens a SQLite connection with sensible settings for our use
def _connect() -> sqlite3.Connection:
    """Open a connection. WAL mode lets reads and writes coexist better."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    # WAL = write-ahead logging. lets the dashboard read while collectors write
    # without them blocking each other as much. good default for this pattern
    conn.execute("PRAGMA journal_mode=WAL")
    # rows come back as dict-like objects instead of bare tuples, easier to read
    conn.row_factory = sqlite3.Row
    return conn


# writes a batch of telemetry records to the samples table in one transaction
def store_records(records: list[TelemetryRecord]) -> int:
    """Persist a list of records. Returns how many were written."""
    if not records:
        return 0

    rows = []
    for r in records:
        # numeric values go in `value`, strings (like route fingerprints) go in
        # `value_str`. keeping them in separate typed columns makes later queries
        # cleaner than cramming everything into one TEXT column
        is_numeric = isinstance(r.value, (int, float)) and not isinstance(r.value, bool)
        rows.append((
            r.timestamp.isoformat(),
            r.run_id,
            r.collector,
            r.metric,
            float(r.value) if is_numeric else None,
            None if is_numeric else (str(r.value) if r.value is not None else None),
            json.dumps(r.metadata),
        ))

    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO samples (ts, run_id, collector, metric, value, value_str, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


# records metadata about a single collector cycle (timing, success, sample count)
def store_run(run_id: str, collector: str, duration_ms: float,
              status: str, sample_count: int) -> None:
    """Log one collector cycle to the runs table for health tracking."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (ts, run_id, collector, duration_ms, status, sample_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), run_id, collector,
             duration_ms, status, sample_count),
        )

# writes an event with its evidence to the events table
def store_event(event_type: str, severity: str, summary: str,
                evidence: dict, timestamp: datetime) -> None:
    """Persist a single detected event."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO events (ts, type, severity, summary, evidence_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp.isoformat(), event_type, severity, summary,
             json.dumps(evidence)),
        )

# ---------- query helpers (used by dashboard + analyzers later) ----------

# fetches recent samples for one collector, newest first
def recent_samples(collector: str, limit: int = 200) -> list[sqlite3.Row]:
    """Most recent samples for a given collector."""
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM samples
            WHERE collector = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (collector, limit),
        )
        return cur.fetchall()


# fetches all samples within a time window: this is the correlation primitive.
# the analyzer asks "what did every collector observe between T and T+window?"
def samples_in_window(start: datetime, end: datetime) -> list[sqlite3.Row]:
    """All samples between start and end, across all collectors.

    This is the core query for ENACT's correlation model: pull everything that
    happened in one time window, then reason across collectors about whether
    the signals moved together.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM samples
            WHERE ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (start.isoformat(), end.isoformat()),
        )
        return cur.fetchall()

# fetches recent events for the dashboard, newest first
def recent_events(limit: int = 100) -> list[sqlite3.Row]:
    """Most recent events across all analyzers."""
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM events
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()

# fetches the most recent successful cycle's timing/status for each collector
def latest_run_per_collector() -> list[sqlite3.Row]:
    """One row per collector: most recent cycle's ts, status, duration, sample_count."""
    with _connect() as conn:
        # this CTE picks the highest id per collector in the runs table.
        # last id = most recent insertion, which is what we want for "last cycle"
        cur = conn.execute(
            """
            WITH latest AS (
                SELECT collector, MAX(id) AS max_id
                FROM runs
                GROUP BY collector
            )
            SELECT r.*
            FROM runs r
            JOIN latest l ON r.id = l.max_id
            ORDER BY r.collector
            """
        )
        return cur.fetchall()

# fetches the three status readouts as a single dict, ready for the dashboard.
# the status collector produces these once per cycle and we want the newest
# of each, this helper keeps the dashboard code simple
def status_snapshot() -> dict:
    """Most recent status readouts (wifi, internet, vpn) as a plain dict."""
    with _connect() as conn:
        cur = conn.execute(
            """
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY metric
                                          ORDER BY ts DESC) AS rn
                FROM samples
                WHERE collector = 'status'
            )
            SELECT * FROM ranked WHERE rn = 1
            """
        )
        rows = cur.fetchall()
    out = {}
    for r in rows:
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        out[r["metric"]] = {
            "value": r["value_str"],
            "ts": r["ts"],
            "meta": meta,
        }
    return out

# fetches the most recent value of each (collector, metric) combination
def latest_metric_snapshots() -> list[sqlite3.Row]:
    """The newest sample for each unique (collector, metric) pair."""
    with _connect() as conn:
        cur = conn.execute(
            """
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY collector, metric
                                          ORDER BY ts DESC) AS rn
                FROM samples
            )
            SELECT * FROM ranked WHERE rn = 1
            ORDER BY collector, metric
            """
        )
        return cur.fetchall()


# fetches latency samples over the last N minutes for one target, oldest first
# used by the sparkline so the time axis flows left to right naturally
def latency_history(target: str, minutes: int = 60) -> list[float]:
    """Latency values for one target over the last N minutes, oldest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT value, meta_json
            FROM samples
            WHERE collector = 'connectivity'
              AND metric = 'latency_ms'
              AND value IS NOT NULL
              AND ts >= ?
            ORDER BY ts ASC
            """,
            (cutoff,),
        )
        # filter by target inside python since target lives in JSON metadata
        rows = cur.fetchall()
        values: list[float] = []
        for r in rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            if meta.get("target") == target:
                values.append(r["value"])
        return values

# fetches latency samples per target over the last N minutes
# used by the live oscilloscope chart for multi-target display
def latency_history_multi(targets: list[str], minutes: int = 30
                           ) -> dict[str, list[tuple[str, float]]]:
    """Per-target latency history as {target: [(iso_ts, value), ...]} oldest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT ts, value, meta_json
            FROM samples
            WHERE collector = 'connectivity'
              AND metric = 'latency_ms'
              AND value IS NOT NULL
              AND ts >= ?
            ORDER BY ts ASC
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    # partition by target. each target gets a list of (timestamp, value) pairs
    out: dict[str, list[tuple[str, float]]] = {t: [] for t in targets}
    for r in rows:
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        target = meta.get("target")
        if target in out:
            out[target].append((r["ts"], r["value"]))
    return out

# fetches recent events with severity for the dashboard event log
def recent_events_compact(limit: int = 15) -> list[dict]:
    """Recent events as plain dicts, ready for JSON. Newest first."""
    rows = recent_events(limit=limit)
    return [
        {
            "ts": r["ts"],
            "type": r["type"],
            "severity": r["severity"],
            "summary": r["summary"],
        }
        for r in rows
    ]

# fetches all the data the dashboard tables need, in one round trip
def dashboard_snapshot() -> dict:
    """One-shot fetch of everything the dashboard tables display."""
    return {
        "collector_health": [dict(r) for r in latest_run_per_collector()],
        "current_metrics": [dict(r) for r in latest_metric_snapshots()],
        "events": recent_events_compact(limit=15),
        "status": status_snapshot(),
    }

# fetches one specific event by its auto-incremented id
def event_by_id(event_id: int) -> sqlite3.Row | None:
    """Retrieve a single event by primary key, or None if not found."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        )
        return cur.fetchone()


# fetches new critical events with id > watermark, used by the alarm to
# detect events we haven't yet shown a popup for
def new_critical_events_since(last_seen_id: int) -> list[dict]:
    """Critical events newer than last_seen_id, oldest first."""
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM events
            WHERE severity = 'critical' AND id > ?
            ORDER BY id ASC
            """,
            (last_seen_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# fetches the samples that are relevant to a given event: the same collector
# types that already appear in the event's evidence, over the specified window
def samples_around_event(event_id: int, seconds_before: int = 60,
                         seconds_after: int = 120) -> dict:
    """Recent samples grouped by collector, spanning around one event's timestamp.

    Used by the incident window to show a live picture of what's happening
    during and after the anomaly. The 'after' window is longer than 'before'
    because we care most about recovery.
    """
    event = event_by_id(event_id)
    if event is None:
        return {}
    event_ts = datetime.fromisoformat(event["ts"])
    window_start = event_ts - timedelta(seconds=seconds_before)
    window_end = datetime.now(timezone.utc)  # "now" so the window grows live
    if window_end > event_ts + timedelta(seconds=seconds_after):
        window_end = event_ts + timedelta(seconds=seconds_after)

    rows = samples_in_window(window_start, window_end)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        grouped.setdefault(r["collector"], []).append({
            "ts": r["ts"],
            "metric": r["metric"],
            "value": r["value"] if r["value"] is not None else r["value_str"],
            "meta": meta,
        })
    return grouped
    
# deletes samples and runs older than the retention window to keep the DB small
def prune_old_data(retention_days: int) -> int:
    """Delete samples/runs older than retention_days. Returns rows deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        deleted = cur.rowcount
        conn.execute("DELETE FROM runs WHERE ts < ?", (cutoff,))
    log.info("pruned %d samples older than %d days", deleted, retention_days)
    return deleted


if __name__ == "__main__":
    # quick manual test: init the db and write a couple of fake records
    # python -m src.storage.database
    from src.utils.records import new_run_id

    init_db()
    rid = new_run_id()
    test = [
        TelemetryRecord(collector="test", metric="latency_ms", value=12.5, run_id=rid),
        TelemetryRecord(collector="test", metric="route_fingerprint",
                        value="abc123def456", run_id=rid),
    ]
    n = store_records(test)
    print(f"wrote {n} records with run_id {rid}")
    for row in recent_samples("test", limit=5):
        print(dict(row))