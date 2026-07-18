"""
Route change analyzer: detects when the network path to a target shifts.

Formula:
    compare the most recent route fingerprint for each target against the
    one before it. if they differ, the path changed: fire an event.
"""

import json
from datetime import datetime, timedelta, timezone

from src.analyzers.base import Analyzer, Event
from src.storage import database


EVIDENCE_WINDOW_SEC = 60
EVENT_DEBOUNCE_SEC = 600


"""
Detects route changes by comparing consecutive route fingerprints per target.
"""
class RouteChangeAnalyzer(Analyzer):
    name = "route_change"

    # runs one analysis pass: check each target for a fingerprint change
    def run(self) -> list[Event]:
        events: list[Event] = []

        rows = database.recent_samples("route", limit=40)
        route_rows = [r for r in rows if r["metric"] == "route_fingerprint"]

        if len(route_rows) < 2:
            return events

        by_target: dict[str, list] = {}
        for r in route_rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            target = meta.get("target", "unknown")
            by_target.setdefault(target, []).append(r)

        for target, samples in by_target.items():
            if len(samples) < 2:
                continue
            event = self._check_target_for_change(target, samples)
            if event is not None:
                events.append(event)

        return events

    # compares the two newest fingerprints for one target, returns an event if changed
    def _check_target_for_change(self, target: str, samples: list) -> Event | None:
        current = samples[0]
        previous = samples[1]

        current_fp = current["value_str"]
        previous_fp = previous["value_str"]

        # stable
        if current_fp == previous_fp:
            return None

        if self._recently_fired(target, current_fp):
            self.log.debug("route change to %s debounced (already fired recently)", target)
            return None

        current_meta = json.loads(current["meta_json"]) if current["meta_json"] else {}
        previous_meta = json.loads(previous["meta_json"]) if previous["meta_json"] else {}
        current_hops = current_meta.get("hop_count", "?")
        previous_hops = previous_meta.get("hop_count", "?")

        change_ts = datetime.fromisoformat(current["ts"])
        evidence = self._build_evidence(
            change_ts, target, previous, current, previous_meta, current_meta
        )

        summary = (f"route to {target} changed "
                   f"(fingerprint {previous_fp} -> {current_fp}, "
                   f"hops {previous_hops} -> {current_hops})")

        self.log.info(summary)

        return Event(
            type=self.name,
            severity="info",
            summary=summary,
            evidence=evidence,
            timestamp=change_ts,
        )

    # returns the current time as a timezone-aware UTC datetime
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # checks for a recent route_change event matching this target + new fingerprint
    def _recently_fired(self, target: str, fingerprint: str) -> bool:
        rows = database.recent_events(limit=20)
        cutoff = self._now_utc() - timedelta(seconds=EVENT_DEBOUNCE_SEC)

        for r in rows:
            if r["type"] != self.name:
                continue
            event_ts = datetime.fromisoformat(r["ts"])
            if event_ts < cutoff:
                continue

            ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
            if ev.get("target") == target and ev.get("new_fingerprint") == fingerprint:
                return True
        return False

    # pulls cross-signal evidence and the before/after hop sequences
    def _build_evidence(self, change_ts: datetime, target: str,
                        previous, current, previous_meta: dict,
                        current_meta: dict) -> dict:
        window_start = change_ts - timedelta(seconds=EVIDENCE_WINDOW_SEC)
        window_end = change_ts + timedelta(seconds=EVIDENCE_WINDOW_SEC)
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

        return {
            "target": target,
            "old_fingerprint": previous["value_str"],
            "new_fingerprint": current["value_str"],
            "old_hops": previous_meta.get("hops", []),
            "new_hops": current_meta.get("hops", []),
            "old_hop_count": previous_meta.get("hop_count"),
            "new_hop_count": current_meta.get("hop_count"),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "concurrent_samples": by_collector,
        }