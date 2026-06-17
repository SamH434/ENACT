"""
Wifi degradation analyzer: detects RSSI drops from rolling baseline.

Formula:
    baseline = median of the last N RSSI samples
    degraded = current RSSI is at least N dB below baseline AND
               current RSSI is below an absolute "concerning" threshold

RSSI lives in negative dBm: -30 is excellent (close to AP), -90 is marginal
(barely connected). Drops are measured in absolute dB, not as a ratio
"""

import json
from datetime import datetime, timedelta, timezone
from statistics import median

from src.analyzers.base import Analyzer, Event
from src.storage import database


# tunables: conservative defaults sized for typical home/office Wifi
LOOKBACK_LIMIT = 100              # how many recent wifi samples to inspect
BASELINE_MIN_SAMPLES = 5          # don't fire until we have at least this much data
RSSI_DROP_DB = 15                 # how many dB below baseline counts as degraded
CONCERNING_RSSI_DBM = -70         # current must be at or below this to fire
EVIDENCE_WINDOW_SEC = 60          # window for cross-signal evidence
EVENT_DEBOUNCE_SEC = 120          # don't re-fire the same degradation


"""
Detects Wifi degradation by comparing current RSSI against a rolling baseline.

Reads recent current_rssi_dbm samples, computes a median baseline, fires
when the most recent reading has dropped at least RSSI_DROP_DB below baseline
AND is below the CONCERNING_RSSI_DBM floor. Includes a debounce so one
sustained degradation produces one event, not a flood.
"""
class WifiDegradationAnalyzer(Analyzer):
    name = "wifi_degradation"

    # runs one analysis pass: check current RSSI against rolling baseline
    def run(self) -> list[Event]:
        events: list[Event] = []

        # pull recent wifi samples, newest first. We ask for plenty because
        # the wifi collector emits many records per cycle (one per nearby AP
        # plus connection records), but we only care about current_rssi_dbm
        rows = database.recent_samples("wifi", limit=LOOKBACK_LIMIT)
        rssi_rows = [r for r in rows if r["metric"] == "current_rssi_dbm"
                     and r["value"] is not None]

        if len(rssi_rows) < BASELINE_MIN_SAMPLES + 1:
            # not enough data, also covers the "host on Ethernet" case
            # where current_rssi_dbm records never get produced
            return events

        # newest first: current is rssi_rows[0], baseline from the rest
        current = rssi_rows[0]
        baseline_values = [r["value"] for r in rssi_rows[1:BASELINE_MIN_SAMPLES + 1]]
        baseline_dbm = median(baseline_values)
        current_dbm = current["value"]

        # how far below baseline are we? positive number = degradation
        drop_db = baseline_dbm - current_dbm

        # both conditions must hold: meaningful drop AND below concerning floor
        if drop_db < RSSI_DROP_DB:
            return events
        if current_dbm > CONCERNING_RSSI_DBM:
            return events

        # debounce: don't re-fire if we already flagged this degradation recently
        if self._recently_fired():
            self.log.debug("RSSI dropped %.0f dB but debounced", drop_db)
            return events

        change_ts = datetime.fromisoformat(current["ts"])
        meta = json.loads(current["meta_json"]) if current["meta_json"] else {}
        ssid = meta.get("ssid", "unknown")
        bssid = meta.get("bssid", "unknown")
        channel = meta.get("channel")

        evidence = self._build_evidence(change_ts, current_dbm, baseline_dbm,
                                        drop_db, ssid, bssid, channel)

        summary = (f"Wifi RSSI dropped to {current_dbm:.0f} dBm "
                   f"(baseline {baseline_dbm:.0f} dBm, drop {drop_db:.0f} dB) "
                   f"on {ssid}")

        self.log.warning(summary)

        return [Event(
            type=self.name,
            severity="warning",
            summary=summary,
            evidence=evidence,
            timestamp=change_ts,
        )]

    # returns the current time as a timezone aware UTC datetime
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # checks the events table for a recent wifi_degradation event, used for debouncing
    def _recently_fired(self) -> bool:
        rows = database.recent_events(limit=10)
        cutoff = self._now_utc() - timedelta(seconds=EVENT_DEBOUNCE_SEC)
        for r in rows:
            if r["type"] != self.name:
                continue
            event_ts = datetime.fromisoformat(r["ts"])
            if event_ts >= cutoff:
                return True
        return False

    # pulls cross-signal evidence: what was every collector observing in this window?
    def _build_evidence(self, change_ts: datetime, current_dbm: float,
                        baseline_dbm: float, drop_db: float,
                        ssid: str, bssid: str, channel: int | None) -> dict:
        window_start = change_ts - timedelta(seconds=EVIDENCE_WINDOW_SEC)
        window_end = change_ts + timedelta(seconds=EVIDENCE_WINDOW_SEC)

        window_rows = database.samples_in_window(window_start, window_end)

        # bucket samples by collector for cross-signal context
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
            "current_rssi_dbm": current_dbm,
            "baseline_rssi_dbm": baseline_dbm,
            "drop_db": drop_db,
            "ssid": ssid,
            "bssid": bssid,
            "channel": channel,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "concurrent_samples": by_collector,
        }