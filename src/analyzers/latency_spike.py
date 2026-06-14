"""
Latency spike analyzer: detects when ping latency exceeds rolling baseline.

Formula:
    baseline = median of the last N successful latency samples
    spike    = current latency is more than (multiplier * baseline) AND
               current latency exceeds an absolute floor (so 3x of 5ms doesn't
               fire on healthy networks where small noise is normal)

When a spike fires, we pull samples from the same 30 second window across 
ALL collectors and attach them as evidence. 

"latency to 1.1.1.1 spiked to 142ms (baseline 12ms), 
and during the same window RSSI dropped from -55 to
-78 dBm" tells you it was a Wi-Fi problem, not an ISP problem.
"""

import json
from datetime import datetime, timedelta
from statistics import median

from src.analyzers.base import Analyzer, Event
from src.storage import database


# tunables: conservative defaults that fire on real degradation but not on
# micro-jitter. adjust later once we have real observed data
LOOKBACK_LIMIT = 200          # how many recent samples to pull when looking for spikes
BASELINE_MIN_SAMPLES = 5      # don't fire until we have at least this much data
SPIKE_MULTIPLIER = 3.0        # current > multiplier * baseline = spike
ABSOLUTE_FLOOR_MS = 50        # don't fire below this even if multiplier hit
EVIDENCE_WINDOW_SEC = 30      # how wide an evidence window to grab around the spike


"""
Detects latency spikes in connectivity samples and attaches cross signal evidence.

Reads recent ping samples per target, computes a rolling median baseline,
flags any current reading that exceeds the baseline by a configured multiplier
above an absolute floor. 

For each spike, pulls samples from all collectors in
the same time window so the event explains itself.
"""
class LatencySpikeAnalyzer(Analyzer):
    name = "latency_spike"

    # runs one analysis pass: check every recent latency sample for spikes
    def run(self) -> list[Event]:
        events: list[Event] = []

        # pull recent latency samples, they come back newest-first
        # ask for plenty of headroom so the baseline has enough data
        rows = database.recent_samples("connectivity", limit=LOOKBACK_LIMIT)
        latency_rows = [r for r in rows if r["metric"] == "latency_ms"
                        and r["value"] is not None]

        if len(latency_rows) < BASELINE_MIN_SAMPLES + 1:
            # not enough data yet, normal on cold start
            return events

        # group by target: a spike to 1.1.1.1 isn't the same event as one to 8.8.8.8
        by_target: dict[str, list] = {}
        for r in latency_rows:
            # target lives inside meta_json, parse it out
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            target = meta.get("target", "unknown")
            by_target.setdefault(target, []).append(r)

        # for each target, check whether the latest reading is a spike
        for target, samples in by_target.items():
            event = self._check_target_for_spike(target, samples)
            if event is not None:
                events.append(event)

        return events

    # checks one target's recent samples, returns an event if the latest is a spike
    def _check_target_for_spike(self, target: str, samples: list) -> Event | None:
        if len(samples) < BASELINE_MIN_SAMPLES + 1:
            return None

        # samples come in newest-first. latest is samples[0], baseline is the
        # median of the rest. simple, robust, explainable.
        current = samples[0]
        baseline_values = [s["value"] for s in samples[1:BASELINE_MIN_SAMPLES + 1]]
        baseline_ms = median(baseline_values)
        current_ms = current["value"]

        # both conditions must hold to fire: multiplier exceeded AND above floor
        if current_ms < ABSOLUTE_FLOOR_MS:
            return None
        if current_ms < baseline_ms * SPIKE_MULTIPLIER:
            return None

        # we have a spike, build the evidence by pulling everything that
        # happened in a 30-second window centered on the spike
        spike_ts = datetime.fromisoformat(current["ts"])
        evidence = self._build_evidence(spike_ts, current_ms, baseline_ms, target)

        summary = (f"latency to {target} spiked to {current_ms:.0f}ms "
                   f"(baseline {baseline_ms:.0f}ms)")

        self.log.warning(summary)

        return Event(
            type=self.name,
            severity="warning",
            summary=summary,
            evidence=evidence,
            timestamp=spike_ts,
        )

    # pulls cross-signal evidence: what was every collector observing in this window?
    def _build_evidence(self, spike_ts: datetime, current_ms: float,
                        baseline_ms: float, target: str) -> dict:
        # window straddles the spike timestamp so we catch slightly-earlier signals too
        # a Wi-Fi RSSI drop usually precedes the latency it causes by a couple seconds
        window_start = spike_ts - timedelta(seconds=EVIDENCE_WINDOW_SEC)
        window_end = spike_ts + timedelta(seconds=EVIDENCE_WINDOW_SEC)

        window_rows = database.samples_in_window(window_start, window_end)

        # bucket the window's samples by collector for readable evidence
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
            "spike_ms": current_ms,
            "baseline_ms": baseline_ms,
            "multiplier_observed": round(current_ms / baseline_ms, 2)
                                   if baseline_ms > 0 else None,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "concurrent_samples": by_collector,
        }