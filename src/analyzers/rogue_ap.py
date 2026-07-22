"""
Rogue AP heuristic analyzer: detects known SSIDs advertised from new BSSIDs.

The specific pattern this catches: when an SSID you've seen before starts
being broadcast by a BSSID that wasn't previously associated with it. That's
one signature of an evil-twin AP standing up alongside a legitimate one to
try to attract associations.

Important honesty caveats built into the design:
    - This detects "unusual condition", not "attack in progress". Info severity
      is deliberate: mesh networks, roaming APs, corporate deployments, and
      pop-up guest networks all produce this pattern legitimately.
    - The analyzer is PASSIVE. It reads what the OS already sees during normal
      Wi-Fi scanning. It does not probe suspected APs, does not attempt to
      associate with them, does not send any packets.
    - The event summary uses hedged language ("new BSSID observed for SSID X"),
      not accusatory language ("evil twin detected"). Deliberate: over-claiming
      would produce alarm fatigue and misrepresent the tool's actual capability.

WARNING FOR FUTURE MAINTAINERS: Do NOT add "active verification" (probing,
deauth, association attempts) to this analyzer. Passive detection is legal
and safe in every jurisdiction; active testing of unauthorized APs is not.
Keep this analyzer read-only against the storage layer.
"""

import json
from datetime import datetime, timedelta, timezone

from src.analyzers.base import Analyzer, Event
from src.storage import database


LOOKBACK_LIMIT = 2000            # generous window: covers ~10 wifi collector cycles
BASELINE_MIN_SAMPLES = 20        # need enough history to define "known"
KNOWN_LOOKBACK_SEC = 86400 * 7   # 7 days: how far back "known" extends
EVIDENCE_WINDOW_SEC = 60
EVENT_DEBOUNCE_SEC = 3600        # 1 hour: don't re-fire for the same new BSSID


"""
Detects when a known SSID appears advertised from a new BSSID.
"""
class RogueAPAnalyzer(Analyzer):
    name = "rogue_ap"

    # runs one analysis pass: build history, find new BSSIDs per known SSID
    def run(self) -> list[Event]:
        events: list[Event] = []

        rows = database.recent_samples("wifi", limit=LOOKBACK_LIMIT)
        ap_rows = [r for r in rows if r["metric"] == "nearby_ap"]

        if len(ap_rows) < BASELINE_MIN_SAMPLES:
            return events

        # build the (SSID -> {BSSID -> first_seen_ts}) map from history.
        # older samples first so "first_seen" reflects the earliest observation
        history: dict[str, dict[str, str]] = {}
        recent: dict[str, dict[str, str]] = {}  # BSSIDs seen in the newest cycle only

        # rows come back newest-first from recent_samples. we want oldest-first
        # so first_seen represents the actual first observation
        cutoff_iso = (datetime.now(timezone.utc)
                      - timedelta(seconds=KNOWN_LOOKBACK_SEC)).isoformat()
        ap_rows_chrono = sorted(
            (r for r in ap_rows if r["ts"] >= cutoff_iso),
            key=lambda r: r["ts"],
        )

        # identify the "current cycle": the most recent wifi cycle by run_id.
        # BSSIDs appearing there are candidates for "newly observed"
        newest_run_id = ap_rows_chrono[-1]["run_id"] if ap_rows_chrono else None

        for r in ap_rows_chrono:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            ssid = (meta.get("ssid") or "").strip()
            bssid = (meta.get("bssid") or "").strip().lower()
            if not ssid or not bssid:
                continue
            # skip hidden/empty-name networks: their SSID is often blank and
            # they don't have "known" identity anyway. anonymous APs don't
            # meaningfully "match" a familiar SSID
            if ssid.lower() in ("", "hidden", "unknown"):
                continue

            history.setdefault(ssid, {})
            history[ssid].setdefault(bssid, r["ts"])

            if r["run_id"] == newest_run_id:
                recent.setdefault(ssid, {})[bssid] = r["ts"]

        # for each SSID currently visible, check if any of its current BSSIDs
        # first appeared in this most recent cycle. those are the "new" ones
        for ssid, current_bssids in recent.items():
            # need at least one prior BSSID for this SSID or it's just "first
            # time we've seen this network", not a new-BSSID-for-known-SSID
            all_bssids_for_ssid = history.get(ssid, {})
            if len(all_bssids_for_ssid) <= len(current_bssids):
                # every BSSID we've ever seen for this SSID is present in the
                # current cycle. no history to compare against — this SSID is
                # brand new, not "known SSID with new BSSID"
                continue

            for bssid, first_seen_ts in current_bssids.items():
                # is this BSSID "new"? new means: first-seen is in the current
                # cycle window. we check by looking for prior occurrences
                prior_occurrences = [
                    ts for b, ts in all_bssids_for_ssid.items()
                    if b == bssid and ts < first_seen_ts
                ]
                if prior_occurrences:
                    # not new: we've seen this BSSID for this SSID before
                    continue

                # this BSSID is genuinely new for this SSID.
                if self._recently_fired(ssid, bssid):
                    self.log.debug("rogue_ap for %s/%s but debounced", ssid, bssid)
                    continue

                event = self._build_event(ssid, bssid, all_bssids_for_ssid,
                                          current_bssids)
                events.append(event)

        return events

    # returns current time as timezone-aware UTC datetime
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # checks the events table for a recent rogue_ap event with this SSID+BSSID
    def _recently_fired(self, ssid: str, bssid: str) -> bool:
        rows = database.recent_events(limit=50)
        cutoff = self._now_utc() - timedelta(seconds=EVENT_DEBOUNCE_SEC)
        for r in rows:
            if r["type"] != self.name:
                continue
            event_ts = datetime.fromisoformat(r["ts"])
            if event_ts < cutoff:
                continue
            ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
            if ev.get("ssid") == ssid and ev.get("new_bssid") == bssid:
                return True
        return False

    # builds an Event with hedged language and cross-signal evidence
    def _build_event(self, ssid: str, new_bssid: str,
                     known_bssids: dict[str, str],
                     current_bssids: dict[str, str]) -> Event:
        change_ts = self._now_utc()
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

        # pull additional context about this specific new BSSID: what channel,
        # what signal, what auth type. useful for the analyst reviewing the event
        new_bssid_meta = self._latest_meta_for_bssid(new_bssid)

        # deliberately hedged summary: report the observation, don't judge intent
        summary = (f"new BSSID observed for SSID '{ssid}': {new_bssid} "
                   f"(previously known BSSIDs: {len(known_bssids) - 1})")
        self.log.info(summary)

        return Event(
            type=self.name,
            severity="info",
            summary=summary,
            evidence={
                "ssid": ssid,
                "new_bssid": new_bssid,
                "new_bssid_channel": new_bssid_meta.get("channel"),
                "new_bssid_signal_pct": new_bssid_meta.get("signal_pct"),
                "new_bssid_auth": new_bssid_meta.get("auth"),
                "known_bssid_count": len(known_bssids) - 1,
                "known_bssids": [b for b in known_bssids if b != new_bssid],
                "current_bssids_visible": list(current_bssids.keys()),
                "note": ("Heuristic observation, not a determination of intent. "
                         "Mesh routers, corporate APs, and roaming networks can "
                         "produce this pattern legitimately."),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "concurrent_samples": by_collector,
            },
            timestamp=change_ts,
        )

    # fetches the most recent metadata for a specific BSSID for context
    def _latest_meta_for_bssid(self, bssid: str) -> dict:
        rows = database.recent_samples("wifi", limit=200)
        for r in rows:
            if r["metric"] != "nearby_ap":
                continue
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            if (meta.get("bssid") or "").lower() == bssid.lower():
                return meta
        return {}   