"""
Incident window for ENACT.

Opens a dedicated pywebview window focused on ONE critical event: what happened,
what's still degraded, what's recovering. Launched by the main dashboard as a
separate subprocess when a critical event fires, so it has its own event loop
independent of the main window's.

Unlike the main dashboard which tries to give a complete network overview, the
incident window is purpose-built for one thing: understanding and monitoring a
specific anomaly until it clears. Layout is optimized around that goal.

Run manually with:
    python -m src.dashboard.incident <event_id>
"""

import json
import sys
import webview

from src.storage import database


WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 720
REFRESH_MS = 1000


"""
Bridge exposed to the incident window's JavaScript.

Two methods: get_incident_state() returns everything the window needs to render
in one call (event details, evidence, recent samples), and mark_acknowledged()
lets the user dismiss the alarm without the main dashboard re-firing it.
"""
class IncidentAPI:

    # binds this bridge to one specific event id at construction time.
    # the window is dedicated to that event throughout its lifetime.
    def __init__(self, event_id: int) -> None:
        self.event_id = event_id

    # returns the full state the incident UI needs: the event, its evidence,
    # and any samples that have arrived since the event fired
    def get_incident_state(self) -> dict:
        event = database.event_by_id(self.event_id)
        if event is None:
            return {"error": f"event {self.event_id} not found"}
        evidence = json.loads(event["evidence_json"]) if event["evidence_json"] else {}
        samples = database.samples_around_event(self.event_id)
        return {
            "event": {
                "id": event["id"],
                "ts": event["ts"],
                "type": event["type"],
                "severity": event["severity"],
                "summary": event["summary"],
            },
            "evidence": evidence,
            "samples": samples,
        }

    # the "MARK ACKNOWLEDGED" button. currently just closes the window.
    # future enhancement: write ack state to a table so events show as
    # handled in the main dashboard
    def close_window(self) -> None:
        for w in webview.windows:
            w.destroy()


_INCIDENT_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>ENACT · Incident</title>
<style>
:root {
    --bg: #000000;
    --bg-panel: #0a0505;
    --amber: #d7af00;
    --amber-bright: #ffb000;
    --cyan: #00afff;
    --cyan-dim: #5a7e8a;
    --red: #ff3030;
    --red-bright: #ff5050;
    --red-dim: #b04040;
    --red-mute: rgba(255, 48, 48, 0.15);
    --green-dim: #5fcf5f;
    --yellow: #ffd700;
    --text-mute: #7a8a90;
}

* { box-sizing: border-box; }

html, body {
    margin: 0;
    padding: 0;
    height: 100vh;
    background: var(--bg);
    color: var(--amber);
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 13px;
    overflow: hidden;
}

/* full-window layout: alarm banner across top, then a three-region body */
#app {
    display: grid;
    grid-template-rows: 90px 1fr 50px;
    height: 100vh;
    padding: 10px;
    gap: 10px;
}

/* the alarm banner across the top: CRITICAL glow, event summary, elapsed time */
#alarm {
    border: 3px solid var(--red);
    background: linear-gradient(180deg, rgba(255, 48, 48, 0.20), rgba(0,0,0,0.6));
    box-shadow: 0 0 25px rgba(255, 48, 48, 0.4), inset 0 0 40px rgba(255, 48, 48, 0.1);
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    padding: 0 20px;
    gap: 20px;
    animation: alarm-pulse 2s ease-in-out infinite;
}
@keyframes alarm-pulse {
    0%, 100% { box-shadow: 0 0 25px rgba(255, 48, 48, 0.4), inset 0 0 40px rgba(255, 48, 48, 0.1); }
    50%      { box-shadow: 0 0 45px rgba(255, 48, 48, 0.7), inset 0 0 60px rgba(255, 48, 48, 0.2); }
}
#alarm .badge {
    background: var(--red);
    color: black;
    padding: 8px 18px;
    font-weight: bold;
    font-size: 18px;
    letter-spacing: 2px;
}
#alarm .summary {
    color: var(--red-bright);
    font-weight: bold;
    font-size: 16px;
    line-height: 1.3;
}
#alarm .summary .type {
    color: var(--yellow);
    font-size: 11px;
    letter-spacing: 1px;
    display: block;
    margin-bottom: 4px;
}
#alarm .elapsed {
    text-align: right;
    color: var(--red-bright);
}
#alarm .elapsed .label {
    color: var(--text-mute);
    font-size: 10px;
    letter-spacing: 1px;
}
#alarm .elapsed .value {
    font-size: 22px;
    font-weight: bold;
    letter-spacing: 1px;
}

/* body: 2x2 grid of incident-focused panels */
#body {
    display: grid;
    grid-template-rows: 1fr 1fr;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    min-height: 0;
}

.panel {
    border: 2px solid var(--red-dim);
    background: var(--bg-panel);
    padding: 18px 14px 8px;
    position: relative;
    min-height: 0;
    overflow: visible;
    display: flex;
    flex-direction: column;
}
.panel-title {
    position: absolute;
    top: -10px;
    left: 14px;
    background: var(--bg);
    padding: 0 8px;
    color: var(--red-bright);
    font-weight: bold;
    letter-spacing: 1px;
    font-size: 11px;
}

/* evidence panel: shows the "why" of the anomaly, prominent bullet-value pairs */
.kv-list { margin: 0; padding: 0; list-style: none; }
.kv-list li {
    display: grid;
    grid-template-columns: 130px 1fr;
    padding: 4px 0;
    border-bottom: 1px solid rgba(255, 48, 48, 0.08);
    align-items: baseline;
}

.kv-list li.nested {
    padding-left: 8px;
    border-bottom: 1px solid rgba(255, 48, 48, 0.03);
}
.kv-list li.nested .k { color: var(--cyan-dim); font-weight: normal; font-size: 10px; }
.kv-list li.nested .v { font-weight: normal; font-size: 12px; }

.kv-list .k { color: var(--text-mute); font-size: 11px; letter-spacing: 0.5px; }
.kv-list .v { color: var(--amber-bright); font-weight: bold; }
.kv-list .v.hot { color: var(--red-bright); }



/* recent samples table: chronological live view */
.scroll-area {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
}
.scroll-area::-webkit-scrollbar { width: 6px; }
.scroll-area::-webkit-scrollbar-thumb { background: rgba(255, 48, 48, 0.2); }
table.samples {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}
table.samples th {
    text-align: left;
    color: var(--red-bright);
    font-weight: bold;
    padding: 4px 6px 4px 0;
    border-bottom: 1px solid rgba(255, 48, 48, 0.2);
    letter-spacing: 0.5px;
    font-size: 10px;
    position: sticky;
    top: 0;
    background: var(--bg-panel);
}
table.samples td {
    color: var(--amber);
    padding: 4px 6px 4px 0;
    border-bottom: 1px solid rgba(255, 48, 48, 0.05);
}
table.samples td.ok  { color: var(--green-dim); }
table.samples td.bad { color: var(--red-bright); font-weight: bold; }
table.samples td.ts  { color: var(--cyan); font-size: 11px; }

/* status readout: what's still degraded vs recovered */
.status-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-top: 4px;
}
.status-block {
    border: 1px solid rgba(255, 48, 48, 0.25);
    padding: 10px 12px;
    background: rgba(255, 48, 48, 0.03);
}
.status-block.recovered {
    border-color: rgba(95, 207, 95, 0.35);
    background: rgba(95, 207, 95, 0.05);
}
.status-block .label {
    color: var(--text-mute);
    font-size: 10px;
    letter-spacing: 1px;
    margin-bottom: 6px;
}
.status-block.recovered .label { color: var(--green-dim); }
.status-block .value {
    font-size: 24px;
    font-weight: bold;
    color: var(--red-bright);
}
.status-block.recovered .value { color: var(--green-dim); }
.status-block .sub {
    color: var(--text-mute);
    font-size: 11px;
    margin-top: 4px;
}

/* action bar at the bottom */
#actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 10px;
    border-top: 1px solid rgba(255, 48, 48, 0.2);
}
#actions .hint {
    color: var(--text-mute);
    font-size: 11px;
    letter-spacing: 1px;
}
.btn {
    background: transparent;
    color: var(--red-bright);
    border: 2px solid var(--red);
    padding: 8px 20px;
    font-family: inherit;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 1px;
    cursor: pointer;
    transition: all 0.15s ease;
}
.btn:hover {
    background: var(--red);
    color: black;
}

.loading { color: var(--cyan); padding: 20px; text-align: center; }
</style>
</head>
<body>

<div id="app">

    <!-- top: alarm banner -->
    <div id="alarm">
        <div class="badge">⚠ CRITICAL</div>
        <div class="summary">
            <span class="type" id="alarm-type">initializing</span>
            <span id="alarm-summary">loading incident data...</span>
        </div>
        <div class="elapsed">
            <div class="label">TIME SINCE ONSET</div>
            <div class="value" id="alarm-elapsed">--:--</div>
        </div>
    </div>

    <!-- body: 2x2 incident panels -->
    <div id="body">

        <!-- top-left: current status of the affected subsystem -->
        <div class="panel">
            <span class="panel-title">[ SUBSYSTEM STATUS ]</span>
            <div class="status-grid" id="status-grid">
                <div class="loading">[ initializing ]</div>
            </div>
        </div>

        <!-- top-right: evidence dict, the "why" of the event -->
        <div class="panel">
            <span class="panel-title">[ EVIDENCE ]</span>
            <div class="scroll-area">
                <ul class="kv-list" id="evidence-list">
                    <li class="loading">[ initializing ]</li>
                </ul>
            </div>
        </div>

        <!-- bottom (full width): live sample stream since event -->
        <div class="panel" style="grid-column: 1 / -1;">
            <span class="panel-title">[ LIVE SAMPLES · SINCE ONSET ]</span>
            <div class="scroll-area">
                <table class="samples" id="samples-table">
                    <thead><tr>
                        <th>TIME</th>
                        <th>COLLECTOR</th>
                        <th>METRIC</th>
                        <th>VALUE</th>
                        <th>CONTEXT</th>
                    </tr></thead>
                    <tbody><tr><td colspan="5" class="loading">[ waiting for samples ]</td></tr></tbody>
                </table>
            </div>
        </div>

    </div>

    <!-- action bar -->
    <div id="actions">
        <span class="hint">INCIDENT WINDOW · UPDATES LIVE · ACKNOWLEDGE TO CLOSE</span>
        <button class="btn" id="ack-btn">MARK ACKNOWLEDGED</button>
    </div>

</div>

<script>
const REFRESH_MS = REFRESH_MS_PLACEHOLDER;

/* HTML-escape for safety when interpolating strings into the DOM */
function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, c => ({
        "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    })[c]);
}

/* format a duration in ms as MM:SS */
function formatElapsed(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
}

/* short absolute time for the samples table: HH:MM:SS */
function formatClock(isoTs) {
    const d = new Date(isoTs);
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0") + ":" +
           String(d.getSeconds()).padStart(2, "0");
}

/* format a value: floats to 1 decimal, ints raw, strings unchanged */
function formatValue(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
        return Number.isInteger(v) ? v.toString() : v.toFixed(1);
    }
    return String(v);
}

let eventStartMs = null;   // ms since epoch, captured on first data load

/* render the alarm banner with the event summary and running elapsed clock */
function renderAlarm(event) {
    document.getElementById("alarm-type").textContent = event.type.toUpperCase();
    document.getElementById("alarm-summary").textContent = event.summary;
    if (eventStartMs === null) {
        eventStartMs = new Date(event.ts).getTime();
    }
}

/* runs frequently on its own timer to keep the elapsed display ticking */
function tickElapsed() {
    if (eventStartMs === null) return;
    const elapsed = Date.now() - eventStartMs;
    document.getElementById("alarm-elapsed").textContent = formatElapsed(elapsed);
}

/* renders the subsystem status blocks: currently degraded vs recovered signals.
   this is heuristic: for a DNS outage we show DNS current status, for a latency
   spike we show latency, etc. */
function renderStatus(eventType, samples) {
    const grid = document.getElementById("status-grid");
    const blocks = [];

    // for each collector, take its most recent sample of each metric
    // and decide "still bad" vs "recovered". heuristic per collector type.
    const latest = {};
    for (const [collector, points] of Object.entries(samples || {})) {
        for (const p of points) {
            const key = `${collector}:${p.metric}`;
            if (!latest[key] || new Date(p.ts) > new Date(latest[key].ts)) {
                latest[key] = { collector, ...p };
            }
        }
    }

    // event-type-specific interpretations
    if (eventType === "dns_outage") {
        // was any DNS sample in the last 30 seconds successful?
        const recentDns = Object.values(latest).filter(p => p.collector === "dns");
        const recentOk = recentDns.some(p => p.meta && p.meta.success === true);
        blocks.push({
            recovered: recentOk,
            label: "DNS RESOLUTION",
            value: recentOk ? "RECOVERED" : "DEGRADED",
            sub: recentOk ? "recent lookups successful" : "recent lookups still failing",
        });
    } else if (eventType === "latency_spike") {
        const lat = latest["connectivity:latency_ms"];
        blocks.push({
            recovered: false,   // needs baseline comparison, keep as degraded for now
            label: "PING LATENCY",
            value: lat ? `${formatValue(lat.value)} ms` : "no data",
            sub: lat ? `to ${lat.meta.target || "unknown"}` : "",
        });
    } else if (eventType === "route_change") {
        const route = latest["route:route_fingerprint"];
        blocks.push({
            recovered: false,
            label: "CURRENT ROUTE",
            value: route ? String(route.value).substring(0, 12) : "no data",
            sub: route ? `to ${route.meta.target || "unknown"}` : "",
        });
    } else if (eventType === "wifi_degradation") {
        const rssi = latest["wifi:current_rssi_dbm"];
        blocks.push({
            recovered: false,
            label: "CURRENT RSSI",
            value: rssi ? `${formatValue(rssi.value)} dBm` : "no data",
            sub: rssi ? `${rssi.meta.ssid || "unknown"}` : "",
        });
    }

    // always show ping latency as a "network health" reference regardless of event type
    if (eventType !== "latency_spike") {
        const lat = latest["connectivity:latency_ms"];
        blocks.push({
            recovered: lat && lat.value !== null && lat.value < 50,
            label: "PING LATENCY",
            value: lat && lat.value !== null ? `${formatValue(lat.value)} ms` : "—",
            sub: lat ? `to ${lat.meta.target || "?"}` : "",
        });
    }

    if (blocks.length === 0) {
        grid.innerHTML = `<div class="loading">[ awaiting data ]</div>`;
        return;
    }

    grid.innerHTML = blocks.map(b => `
        <div class="status-block ${b.recovered ? 'recovered' : ''}">
            <div class="label">${escapeHtml(b.label)}</div>
            <div class="value">${escapeHtml(b.value)}</div>
            <div class="sub">${escapeHtml(b.sub)}</div>
        </div>
    `).join("");
}

/* renders the evidence key-value list. objects like per_hostname get expanded
   into nested rows instead of dumped as JSON, otherwise they're unreadable */
function renderEvidence(evidence) {
    const list = document.getElementById("evidence-list");
    const items = [];
    for (const [k, v] of Object.entries(evidence)) {
        if (k === "concurrent_samples") continue;
        if (v === null || v === undefined) continue;

        // special case: per-hostname dict gets expanded into readable rows
        if (k === "per_hostname" && typeof v === "object" && !Array.isArray(v)) {
            items.push(`<li>
                <span class="k">${escapeHtml(k.toUpperCase())}</span>
                <span class="v"></span>
            </li>`);
            for (const [host, stats] of Object.entries(v)) {
                const success = stats.success || 0;
                const failure = stats.failure || 0;
                const isHot = failure > success;
                items.push(`<li class="nested">
                    <span class="k">&nbsp;&nbsp;${escapeHtml(host)}</span>
                    <span class="v ${isHot ? 'hot' : ''}">
                        ${success} ok · ${failure} fail
                    </span>
                </li>`);
            }
            continue;
        }

        // hop lists get shown as arrow-separated hops
        if ((k === "old_hops" || k === "new_hops") && Array.isArray(v)) {
            items.push(`<li>
                <span class="k">${escapeHtml(k.toUpperCase())}</span>
                <span class="v">${escapeHtml(v.join(" → "))}</span>
            </li>`);
            continue;
        }

        // window_start / window_end: strip the microseconds and timezone for readability
        if (k === "window_start" || k === "window_end") {
            const clean = String(v).split(".")[0].replace("T", " ");
            items.push(`<li>
                <span class="k">${escapeHtml(k.toUpperCase())}</span>
                <span class="v">${escapeHtml(clean)}</span>
            </li>`);
            continue;
        }

        // numeric floats: round to 2 places for readability
        let display;
        if (typeof v === "number") {
            display = Number.isInteger(v) ? String(v) : v.toFixed(2);
        } else if (typeof v === "object") {
            display = JSON.stringify(v);
        } else {
            display = String(v);
        }

        const isHot = (k.includes("rate") && typeof v === "number" && v >= 0.5)
                   || (k === "multiplier_observed" && typeof v === "number" && v >= 3)
                   || (k === "drop_db" && typeof v === "number" && v >= 15);
        items.push(`<li>
            <span class="k">${escapeHtml(k.toUpperCase())}</span>
            <span class="v ${isHot ? 'hot' : ''}">${escapeHtml(display)}</span>
        </li>`);
    }
    list.innerHTML = items.length > 0
        ? items.join("")
        : `<li class="loading">[ no evidence available ]</li>`;
}

/* renders the live samples table, sorted newest first for at-a-glance freshness */
function renderSamples(samples) {
    const tbody = document.querySelector("#samples-table tbody");
    // flatten and sort by ts descending
    const flat = [];
    for (const [collector, points] of Object.entries(samples || {})) {
        for (const p of points) {
            flat.push({ collector, ...p });
        }
    }
    flat.sort((a, b) => (a.ts < b.ts ? 1 : -1));

    if (flat.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading">[ no samples yet ]</td></tr>`;
        return;
    }

    // context column: pull a compact summary from the metadata
    tbody.innerHTML = flat.slice(0, 40).map(p => {
        const isFailure = (p.meta && p.meta.success === false)
                       || (p.value === null || p.value === undefined);
        const valClass = isFailure ? "bad" : (p.meta && p.meta.success === true ? "ok" : "");
        // one-line meta summary
        const contextBits = [];
        if (p.meta) {
            if (p.meta.target)   contextBits.push(`target=${p.meta.target}`);
            if (p.meta.hostname) contextBits.push(`host=${p.meta.hostname}`);
            if (p.meta.ssid)     contextBits.push(`ssid=${p.meta.ssid}`);
            if (p.meta.error)    contextBits.push(`err=${p.meta.error}`);
        }
        return `<tr>
            <td class="ts">${formatClock(p.ts)}</td>
            <td>${escapeHtml(p.collector.toUpperCase())}</td>
            <td>${escapeHtml(p.metric)}</td>
            <td class="${valClass}">${escapeHtml(formatValue(p.value))}</td>
            <td>${escapeHtml(contextBits.join(" · "))}</td>
        </tr>`;
    }).join("");
}

/* poll the Python side for the full incident state */
async function tick() {
    try {
        const state = await window.pywebview.api.get_incident_state();
        if (state.error) {
            console.error("[ENACT] incident window error:", state.error);
            return;
        }
        renderAlarm(state.event);
        renderStatus(state.event.type, state.samples);
        renderEvidence(state.evidence);
        renderSamples(state.samples);
    } catch (e) {
        // pywebview may not be ready, retry
    }
}

/* wire up the acknowledge button */
function wireActions() {
    document.getElementById("ack-btn").addEventListener("click", async () => {
        try { await window.pywebview.api.close_window(); }
        catch (e) { /* window is closing, ignore */ }
    });
}

/* boot */
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
        wireActions();
        setInterval(tickElapsed, 500);   // elapsed timer, pure JS
    });
} else {
    wireActions();
    setInterval(tickElapsed, 500);
}
window.addEventListener("pywebviewready", () => {
    tick();                              // first data pull immediately
    setInterval(tick, REFRESH_MS);       // then keep polling
});
</script>

</body>
</html>
"""


# entry point: launched with the event id as an argv, e.g. python -m src.dashboard.incident 42
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.dashboard.incident <event_id>")
        sys.exit(1)
    try:
        event_id = int(sys.argv[1])
    except ValueError:
        print(f"Invalid event id: {sys.argv[1]}")
        sys.exit(1)

    html = _INCIDENT_HTML.replace("REFRESH_MS_PLACEHOLDER", str(REFRESH_MS))

    api = IncidentAPI(event_id)
    webview.create_window(
        title=f"ENACT · INCIDENT #{event_id}",
        html=html,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
        background_color="#000000",
    )
    webview.start(gui="edgechromium", debug=True)


if __name__ == "__main__":
    main()