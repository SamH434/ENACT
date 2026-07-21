"""
Firewall disabled analyzer: detects when a Windows Defender Firewall profile
transitions from ON to OFF between consecutive collector cycles.
"""

import json
from datetime import datetime, timedelta, timezone

from src.analyzers.base import Analyzer, Event
from src.storage import database


LOOKBACK_LIMIT = 40
EVIDENCE_WINDOW_SEC = 60
EVENT_DEBOUNCE_SEC = 300


"""
Detects firewall profile state transitions from enabled to disabled.
"""
class FirewallDisabledAnalyzer(Analyzer):
    name = "firewall_disabled"

    # runs one analysis pass: for each profile, compare latest two samples
    def run(self) -> list[Event]:
        events: list[Event] = []

        rows = database.recent_samples("firewall", limit=LOOKBACK_LIMIT)
        profile_rows = [r for r in rows if r["metric"] == "firewall_profile_state"]

        if len(profile_rows) < 2:
            return events

        by_profile: dict[str, list] = {}
        for r in profile_rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            profile = meta.get("profile", "unknown")
            by_profile.setdefault(profile, []).append(r)

        for profile, samples in by_profile.items():
            if len(samples) < 2:
                continue
            event = self._check_profile_for_transition(profile, samples)
            if event is not None:
                events.append(event)

        return events

    # compares the two newest samples for one profile, fires if it just went off
    def _check_profile_for_transition(self, profile: str, samples: list) -> Event | None:
        current = samples[0]
        previous = samples[1]

        current_state = (current["value_str"] or "").lower()
        previous_state = (previous["value_str"] or "").lower()

        if previous_state != "on" or current_state == "on":
            return None

        if self._recently_fired(profile):
            self.log.debug("firewall %s disabled but debounced", profile)
            return None

        change_ts = datetime.fromisoformat(current["ts"])
        evidence = self._build_evidence(change_ts, profile, previous, current)

        summary = (f"Windows Defender Firewall {profile} profile "
                   f"transitioned from ON to OFF")
        self.log.warning(summary)

        return Event(
            type=self.name,
            severity="warning",
            summary=summary,
            evidence=evidence,
            timestamp=change_ts,
        )

    # returns the current time as a timezone-aware UTC datetime
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # checks for a recent firewall_disabled event matching this profile
    def _recently_fired(self, profile: str) -> bool:
        rows = database.recent_events(limit=20)
        cutoff = self._now_utc() - timedelta(seconds=EVENT_DEBOUNCE_SEC)
        for r in rows:
            if r["type"] != self.name:
                continue
            event_ts = datetime.fromisoformat(r["ts"])
            if event_ts < cutoff:
                continue
            ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
            if ev.get("profile") == profile:
                return True
        return False

    # pulls cross-signal evidence and profile transition context
    def _build_evidence(self, change_ts: datetime, profile: str,
                        previous, current) -> dict:
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

        previous_meta = json.loads(previous["meta_json"]) if previous["meta_json"] else {}
        current_meta = json.loads(current["meta_json"]) if current["meta_json"] else {}

        return {
            "profile": profile,
            "previous_state": previous["value_str"],
            "current_state": current["value_str"],
            "previous_ts": previous["ts"],
            "current_ts": current["ts"],
            "previous_inbound": previous_meta.get("inbound_default"),
            "current_inbound": current_meta.get("inbound_default"),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "concurrent_samples": by_collector,
        }