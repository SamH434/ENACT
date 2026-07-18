"""
DNS outage analyzer: detects elevated DNS failure rates over a sliding window.

Formula:
    over the last N DNS lookup attempts, what fraction failed?
    failure rate >= warning_threshold  =>  warning event
    failure rate >= critical_threshold =>  critical event
"""

import json
from datetime import datetime, timedelta, timezone

from src.analyzers.base import Analyzer, Event
from src.storage import database

LOOKBACK_LIMIT = 40              # how many recent DNS samples to inspect
MIN_SAMPLES_TO_FIRE = 4
WARNING_THRESHOLD = 0.5          # 50%+ failure rate, warning event
CRITICAL_THRESHOLD = 0.9         # 90%+ failure rate, critical event
EVIDENCE_WINDOW_SEC = 30
EVENT_DEBOUNCE_SEC = 60


"""
Detects DNS outages by computing a failure rate over recent samples.
"""
class DNSOutageAnalyzer(Analyzer):
    name = "dns_outage"

    # runs one analysis pass: compute failure rate, fire event if threshold crossed
    def run(self) -> list[Event]:

        events: list[Event] = []
        rows = database.recent_samples("dns", limit=LOOKBACK_LIMIT)
        dns_rows = [r for r in rows if r["metric"] == "resolution_ms"]

        if len(dns_rows) < MIN_SAMPLES_TO_FIRE:
            return events

        successes = 0
        failures = 0
        for r in dns_rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            if meta.get("success") is True:
                successes += 1
            else:
                failures += 1

        total = successes + failures
        failure_rate = failures / total if total else 0.0

        # below warning threshold, nothing to do
        if failure_rate < WARNING_THRESHOLD:
            return events

        if self._recently_fired():
            self.log.debug("DNS failure rate %.0f%% but debounced", failure_rate * 100)
            return events

        # pick severity based on how bad it is
        severity = "critical" if failure_rate >= CRITICAL_THRESHOLD else "warning"

        event_ts = self._now_utc()

        evidence = self._build_evidence(event_ts, successes, failures, dns_rows)

        summary = (f"DNS failure rate {failure_rate * 100:.0f}% "
                   f"({failures}/{total} lookups failed)")

        if severity == "critical":
            self.log.error(summary)
        else:
            self.log.warning(summary)

        events.append(Event(
            type=self.name,
            severity=severity,
            summary=summary,
            evidence=evidence,
            timestamp=event_ts,
        ))

        return events

    # returns the current time as a timezone aware UTC datetime
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # checks the events table for a recent dns_outage event, used for debouncing
    def _recently_fired(self) -> bool:
        rows = database.recent_events(limit=5)
        cutoff = self._now_utc() - timedelta(seconds=EVENT_DEBOUNCE_SEC)
        for r in rows:
            if r["type"] != self.name:
                continue
            event_ts = datetime.fromisoformat(r["ts"])
            if event_ts >= cutoff:
                return True
        return False

    # pulls cross signal evidence and summarizes which hostnames failed
    def _build_evidence(self, event_ts: datetime, successes: int, failures: int,
                        dns_rows: list) -> dict:
        window_start = event_ts - timedelta(seconds=EVIDENCE_WINDOW_SEC)
        window_end = event_ts + timedelta(seconds=EVIDENCE_WINDOW_SEC)
        window_rows = database.samples_in_window(window_start, window_end)
        by_collector: dict[str, list] = {}
        for r in window_rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            by_collector.setdefault(r["collector"], []).append({
                "ts": r["ts"],
                "metric": r["metric"],
                "value": r["value"] if r["value"] is not None else r["value_str"],
                "meta": meta,
            })

        per_hostname: dict[str, dict] = {}
        for r in dns_rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            host = meta.get("hostname", "unknown")
            stats = per_hostname.setdefault(host, {"success": 0, "failure": 0})
            if meta.get("success") is True:
                stats["success"] += 1
            else:
                stats["failure"] += 1

        total = successes + failures
        return {
            "failures": failures,
            "successes": successes,
            "total": total,
            "failure_rate": round(failures / total, 3) if total else 0,
            "per_hostname": per_hostname,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "concurrent_samples": by_collector,
        }