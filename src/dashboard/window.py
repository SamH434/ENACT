"""
Native window dashboard for ENACT.

Full HTML/CSS dashboard rendered in a pywebview window using WebView2 on
Windows

Architecture:
    Python:    storage queries + js_api bridge (DashboardAPI)
    Browser:   layout, styling, polling, rendering, animation

Run with:
    python -m src.dashboard.window
"""

import json
import webview

from src.storage import database


REFRESH_MS = 1000           # how often tables and event log re-render
CHART_REFRESH_MS = 1000     # how often chart pulls new data points
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 820

# match the connectivity collector's default targets
TRACE_TARGETS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]

# trace colors chosen to fit the amber/cyan palette while staying distinguishable
TRACE_COLORS = {
    "1.1.1.1": "#FFB000",  # amber primary
    "8.8.8.8": "#00D7FF",  # cyan
    "9.9.9.9": "#FF6B6B",  # warm red
}

TRACE_TARGETS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]

# trace colors chosen to fit the amber/cyan palette while staying distinguishable
TRACE_COLORS = {
    "1.1.1.1": "#FFB000",  # amber primary
    "8.8.8.8": "#00D7FF",  # cyan
    "9.9.9.9": "#FF6B6B",  # warm red
}

# human-friendly labels for the chart legend
# the IPs are truth, the names are meaning
TRACE_LABELS = {
    "1.1.1.1": "Cloudflare · 1.1.1.1",
    "8.8.8.8": "Google · 8.8.8.8",
    "9.9.9.9": "Quad9 · 9.9.9.9",
}


"""
JavaScript-callable API exposed to the embedded browser.

Three methods cover everything the dashboard needs. All return plain JSON-able
data, no HTML rendering happens server-side. The browser is responsible for
all presentation.
"""
class DashboardAPI:

    # returns table data (collector health, current metrics, recent events) in one call
    def get_snapshot(self) -> dict:
        return database.dashboard_snapshot()

    # returns per-target latency points for the live oscilloscope
    def get_latency(self) -> dict:
        raw = database.latency_history_multi(TRACE_TARGETS, minutes=30)
        return {
            target: [{"x": ts, "y": val} for ts, val in points]
            for target, points in raw.items()
        }


# the static HTML/CSS/JS that drives the dashboard. python only provides data,
# the browser does layout and rendering. constants get injected at startup
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>ENACT</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/moment.js/2.29.4/moment.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-adapter-moment/1.0.1/chartjs-adapter-moment.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-streaming@2.0.0/dist/chartjs-plugin-streaming.min.js"></script>
<style>
:root {
    --bg: #000000;
    --bg-panel: #050505;
    --amber: #d7af00;
    --amber-bright: #ffb000;
    --amber-dim: #8a6e00;
    --cyan: #00afff;
    --cyan-dim: #5a7e8a;
    --cyan-mute: rgba(0, 175, 255, 0.10);
    --red: #ff3030;
    --red-dim: #b04040;
    --green-dim: #5fcf5f;
    --yellow: #ffd700;
    --text-mute: #5a7e8a;
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

/* full-window CSS grid: header row, body grid, footer row */
#app {
    display: grid;
    grid-template-rows: 70px 1fr 36px;
    grid-template-columns: 1fr;
    height: 100vh;
    padding: 8px;
    gap: 8px;
}

/* top bar: title panel + clock panel side by side */
#topbar {
    display: grid;
    grid-template-columns: 3fr 1fr;
    gap: 8px;
}

/* body: 2x2 grid of the four main panels */
#body {
    display: grid;
    grid-template-rows: 1fr 1fr;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    min-height: 0;
}

/* every panel: bordered, amber outline, internal padding for content */
.panel {
    border: 2px solid var(--amber);
    background: var(--bg-panel);
    padding: 18px 14px 8px;
    position: relative;
    min-height: 0;
    overflow: visible;
    display: flex;
    flex-direction: column;
}

/* panel title sits across the top border like the bracketed labels did */
.panel-title {
    position: absolute;
    top: -10px;
    left: 14px;
    background: var(--bg);
    padding: 0 8px;
    color: var(--amber-bright);
    font-weight: bold;
    letter-spacing: 0.5px;
    font-size: 12px;
}

/* the title bar header has its own styling, less utilitarian */
#header {
    border: 2px solid var(--amber);
    background: var(--bg-panel);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 16px;
    gap: 24px;
}
#header .label {
    background: var(--amber-bright);
    color: var(--bg);
    font-weight: bold;
    padding: 4px 12px;
    letter-spacing: 1px;
}
#header .subtitle {
    color: var(--amber);
    font-weight: bold;
    letter-spacing: 1px;
}
#header .date {
    color: var(--cyan);
    margin-left: auto;
}

/* clock panel: large numeric readout, label above */
#clock {
    border: 2px solid var(--amber);
    background: var(--bg-panel);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 8px;
}
#clock .label {
    color: var(--text-mute);
    font-size: 11px;
    letter-spacing: 1px;
    margin-bottom: 4px;
}
#clock .time {
    color: var(--amber-bright);
    font-weight: bold;
    font-size: 22px;
    letter-spacing: 1px;
}
#clock .ms {
    color: var(--amber);
    font-size: 18px;
}

/* footer hint bar */
#footer {
    border: 2px solid var(--cyan-dim);
    color: var(--cyan-dim);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    letter-spacing: 1px;
}

/* tables: align cells, color headers, mute borders */
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 4px;
    font-size: 13px;
}
th {
    text-align: left;
    color: var(--amber-bright);
    font-weight: bold;
    padding: 4px 8px 4px 0;
    border-bottom: 1px solid var(--cyan-mute);
    letter-spacing: 0.5px;
    font-size: 11px;
}
td {
    color: var(--amber);
    padding: 6px 12px 6px 0;
    border-bottom: 1px solid rgba(0, 175, 255, 0.04);
    vertical-align: top;
}
td.source { color: var(--cyan); }
td.age    { color: var(--cyan); font-size: 12px; }
td.value  { color: var(--amber-bright); font-weight: bold; text-align: right; }
td.right  { text-align: right; }
td.value-left { color: var(--amber-bright); font-weight: bold; text-align: left; }

/* value boxes for event summaries: wrap changing values so the eye can
   spot the actual data amongst the surrounding prose */
.val-box {
    display: inline-block;
    padding: 1px 6px;
    margin: 0 1px;
    border-radius: 2px;
    border: 1px solid rgba(215, 175, 0, 0.35);
    background: rgba(215, 175, 0, 0.06);
    font-weight: bold;
    font-size: 11.5px;
    letter-spacing: 0.3px;
    color: var(--amber-bright);
}
.val-box.val-hash { color: var(--cyan); border-color: rgba(0, 175, 255, 0.40); background: rgba(0, 175, 255, 0.05); }
.val-box.val-ip   { color: var(--amber-bright); border-color: rgba(255, 176, 0, 0.35); }
.val-box.val-num  { color: var(--amber-bright); }
.val-arrow {
    display: inline-block;
    padding: 0 4px;
    color: var(--cyan);
    font-weight: bold;
}

/* in warning-severity rows, tint the boxes yellow; in critical, tint red.
   the box color is the diagnostic signal, not the prose color */
.sev-warning-summary  .val-box { border-color: rgba(255, 215, 0, 0.5); }
.sev-critical-summary .val-box { border-color: rgba(255, 48, 48, 0.5); background: rgba(255, 48, 48, 0.08); }

/* status indicators in collector health table */
.status-ok    { color: var(--green-dim); }
.status-error { color: var(--red); font-weight: bold; }

/* event log severity colors */
.sev-info     { color: var(--cyan); }
.sev-warning  { color: var(--yellow); font-weight: bold; }
.sev-critical { color: var(--red); font-weight: bold; }
.summary-info     { color: var(--cyan); }
.summary-warning  { color: var(--amber); }
.summary-critical { color: var(--red); }

/* the chart panel holds the canvas directly */
#chart-canvas-wrap {
    flex: 1;
    min-height: 0;
    position: relative;
}
#chart-canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
}

/* event log scrolls if too tall, with subtle scrollbar styling */
.scroll-area {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
}
.scroll-area::-webkit-scrollbar { width: 6px; }
.scroll-area::-webkit-scrollbar-thumb { background: var(--cyan-mute); }

/* initial loading state, replaced after first poll */
.loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--cyan);
    letter-spacing: 1px;
}
</style>
</head>
<body>

<div id="app">

    <!-- top bar: title + clock -->
    <div id="topbar">
        <div id="header">
            <span class="label">[ ENACT ]</span>
            <span class="subtitle">ENGINE FOR NETWORK ANOMALY, CONDITION, AND TELEMETRY</span>
            <span class="date" id="header-date"></span>
        </div>
        <div id="clock">
            <div class="label">ACTIVE TIME DISPLAY</div>
            <div><span class="time" id="clock-time">00:00:00</span><span class="ms" id="clock-ms">.000</span></div>
        </div>
    </div>

    <!-- body: 2x2 panel grid -->
    <div id="body">

        <!-- top-left: collector health -->
        <div class="panel">
            <span class="panel-title">[ COLLECTOR HEALTH MONITOR ]</span>
            <table id="tbl-health">
                <thead><tr>
                    <th>UNIT</th><th>LAST</th><th>STATUS</th>
                    <th>DUR</th><th>SAMPLES</th>
                </tr></thead>
                <tbody><tr><td colspan="5" class="loading">[ initializing ]</td></tr></tbody>
            </table>
        </div>

        <!-- top-right: telemetry readout -->
        <div class="panel">
            <span class="panel-title">[ TELEMETRY READOUT ]</span>
            <table id="tbl-metrics">
                <thead><tr>
                    <th>SOURCE</th><th>METRIC</th>
                    <th>VALUE</th><th>AGE</th>
                </tr></thead>
                <tbody><tr><td colspan="4" class="loading">[ initializing ]</td></tr></tbody>
            </table>
        </div>

        <!-- bottom-left: event log -->
        <div class="panel">
            <span class="panel-title">[ EVENT LOG ]</span>
            <div class="scroll-area">
                <table id="tbl-events">
                    <thead><tr>
                        <th>AGE</th><th>SEV</th><th>TYPE</th><th>SUMMARY</th>
                    </tr></thead>
                    <tbody><tr><td colspan="4" class="loading">[ initializing ]</td></tr></tbody>
                </table>
            </div>
        </div>

        <!-- bottom-right: live latency oscilloscope -->
        <div class="panel">
            <span class="panel-title">[ LATENCY TRACE · LIVE OSCILLOSCOPE ]</span>
            <div id="chart-canvas-wrap">
                <canvas id="chart-canvas"></canvas>
            </div>
        </div>

    </div>

    <!-- footer -->
    <div id="footer">PRESS ALT+F4 OR CLOSE WINDOW TO DISENGAGE</div>

</div>

<script>
const TRACE_COLORS = TRACE_COLORS_PLACEHOLDER;
const TRACE_TARGETS = TRACE_TARGETS_PLACEHOLDER;
const TRACE_LABELS = TRACE_LABELS_PLACEHOLDER;
const REFRESH_MS = REFRESH_MS_PLACEHOLDER;
const CHART_REFRESH_MS = CHART_REFRESH_MS_PLACEHOLDER;

/* convert an ISO timestamp to a short "X ago" string */
function ago(isoTs) {
    if (!isoTs) return "?";
    const then = new Date(isoTs);
    if (isNaN(then)) return "?";
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 60)    return secs + "s";
    if (secs < 3600)  return Math.floor(secs / 60) + "m";
    if (secs < 86400) return Math.floor(secs / 3600) + "h";
    return Math.floor(secs / 86400) + "d";
}

/* format a numeric value compactly: floats get one decimal, ints stay whole */
function formatValue(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
        return Number.isInteger(v) ? v.toString() : v.toFixed(1);
    }
    return String(v);
}

/* update the live clock (HH:MM:SS.mmm) and the date in the header */
function tickClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, "0");
    const m = String(now.getMinutes()).padStart(2, "0");
    const s = String(now.getSeconds()).padStart(2, "0");
    const ms = String(now.getMilliseconds()).padStart(3, "0");
    document.getElementById("clock-time").textContent = `${h}:${m}:${s}`;
    document.getElementById("clock-ms").textContent = `.${ms}`;

    const y = now.getFullYear();
    const mo = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    document.getElementById("header-date").textContent = `${y}.${mo}.${d}`;
}

/* render the collector health table from snapshot data */
function renderHealth(rows) {
    const tbody = document.querySelector("#tbl-health tbody");
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="loading">[ no data yet ]</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(r => {
        const statusClass = r.status === "ok" ? "status-ok" : "status-error";
        const statusLabel = r.status === "ok" ? "● NORMAL" : "● ERROR";
        const dur = r.duration_ms ? Math.round(r.duration_ms) + "ms" : "?";
        return `<tr>
            <td class="source">${escapeHtml((r.collector || "?").toUpperCase())}</td>
            <td class="age">${ago(r.ts)}</td>
            <td class="${statusClass}">${statusLabel}</td>
            <td class="value-left">${dur}</td>
            <td class="value-left">${r.sample_count ?? 0}</td>
        </tr>`;
    }).join("");
}

/* render the telemetry readout table */
function renderMetrics(rows) {
    const tbody = document.querySelector("#tbl-metrics tbody");
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="loading">[ no data yet ]</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(r => {
        // numeric values live in value column, strings live in value_str
        const v = r.value !== null && r.value !== undefined ? r.value : r.value_str;
        return `<tr>
            <td class="source">${escapeHtml((r.collector || "?").toUpperCase())}</td>
            <td>${escapeHtml(r.metric || "")}</td>
            <td class="value-left">${escapeHtml(formatValue(v))}</td>
            <td class="age">${ago(r.ts)}</td>
        </tr>`;
    }).join("");
}

/* render the event log with severity coloring */
/* wraps value-like tokens in an event summary with a subtle boxed span so the
   changing data stands out from the surrounding prose. patterns handled:
     - IPs (1.1.1.1, 8.8.8.8, etc.)
     - hex fingerprints (12+ char hex strings)
     - transitions "A -> B" or "A → B"
     - numbers followed by ms, %, etc.
     - hop counts like "hops 9 -> 13"
   the wrapped tokens get a border matching the severity color so the eye
   catches the actual data at a glance */
function boxifySummary(summary) {
    let s = escapeHtml(summary || "");
    // wrap short hex hashes (10+ hex chars = fingerprint-like)
    s = s.replace(/\b([0-9a-f]{10,})\b/g, '<span class="val-box val-hash">$1</span>');
    // wrap dotted-quad IPs
    s = s.replace(/\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b/g,
                  '<span class="val-box val-ip">$1</span>');
    // wrap number+unit patterns like "142ms" "88%"
    s = s.replace(/\b(\d+(?:\.\d+)?(?:ms|%|dBm|Mbps))\b/g,
                  '<span class="val-box val-num">$1</span>');
    // wrap standalone numbers that follow specific keywords (hops N, count N)
    s = s.replace(/\b(hops|count|rate|drop)\s+(\d+)\b/gi,
                  '$1 <span class="val-box val-num">$2</span>');
    // style the transition arrow itself so it visually separates before/after
    s = s.replace(/\s->\s/g, ' <span class="val-arrow">→</span> ');
    return s;
}

function renderEvents(rows) {
    const tbody = document.querySelector("#tbl-events tbody");
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="loading">[ no events ]</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(r => {
        const sev = r.severity || "info";
        const sevClass = `sev-${sev}`;
        const summaryClass = `summary-${sev}`;
        const sevSymbol = sev === "critical" ? "◆ CRIT"
                        : sev === "warning"  ? "▲ WARN"
                        : "● INFO";
        return `<tr>
            <td class="age">${ago(r.ts)}</td>
            <td class="${sevClass}">${sevSymbol}</td>
            <td>${escapeHtml(r.type || "")}</td>
            <td class="${summaryClass} ${sevClass}-summary">${boxifySummary(r.summary)}</td>
        </tr>`;
    }).join("");
}

/* minimal HTML escaping for dynamic content. summaries can contain anything */
function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, c => ({
        "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    })[c]);
}

/* poll the snapshot endpoint and refresh all three tables */
async function tickSnapshot() {
    try {
        const snap = await window.pywebview.api.get_snapshot();
        renderHealth(snap.collector_health);
        renderMetrics(snap.current_metrics);
        renderEvents(snap.events);
    } catch (e) {
        // pywebview bridge not ready yet, retry next tick
    }
}

let chart = null;

/* initialize the streaming line chart for the latency oscilloscope */
function initChart() {
    const ctx = document.getElementById("chart-canvas").getContext("2d");
    const datasets = TRACE_TARGETS.map(target => ({
        label: TRACE_LABELS[target] || target,
        _target: target,   // store the raw target for pullLatency() to key on
        data: [],
        borderColor: TRACE_COLORS[target],
        backgroundColor: TRACE_COLORS[target] + "20",
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.35,
        cubicInterpolationMode: "monotone",
        fill: false,
    }));

    chart = new Chart(ctx, {
        type: "line",
        data: { datasets },
        options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false },
            plugins: {
                legend: {
                    display: true,
                    position: "top",
                    align: "end",
                    labels: {
                        color: "#d7af00",
                        font: { family: "Cascadia Mono, monospace", size: 11 },
                        boxWidth: 12,
                        usePointStyle: false,
                    },
                },
                streaming: {
                    duration: 60000,    // visible time window
                    refresh: CHART_REFRESH_MS,
                    delay: 1500,        // small lag for smoother visual flow
                    onRefresh: pullLatency,
                },
                tooltip: { enabled: false },
            },
            scales: {
                x: {
                    type: "realtime",
                    grid: { color: "rgba(0, 175, 255, 0.10)" },
                    ticks: {
                        color: "#5a7e8a",
                        font: { family: "Cascadia Mono, monospace", size: 10 },
                    },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: "rgba(0, 175, 255, 0.10)" },
                    ticks: {
                        color: "#5a7e8a",
                        font: { family: "Cascadia Mono, monospace", size: 10 },
                        callback: v => v + "ms",
                    },
                    title: {
                        display: true,
                        text: "LATENCY",
                        color: "#5a7e8a",
                        font: { family: "Cascadia Mono, monospace", size: 10 },
                    },
                },
            },
        },
    });
}

/* called by the streaming plugin to pull new latency points from python */
async function pullLatency(chart) {
    try {
        const latency = await window.pywebview.api.get_latency();
        chart.data.datasets.forEach(dataset => {
            const target = dataset._target;
            const incoming = latency[target] || [];
            const lastTs = dataset._lastTs || 0;
            let newest = lastTs;
            for (const point of incoming) {
                const t = Date.parse(point.x);
                if (t > lastTs) {
                    dataset.data.push({ x: t, y: point.y });
                    if (t > newest) newest = t;
                }
            }
            dataset._lastTs = newest;
        });
    } catch (e) {
        // ignore poll failures
    }
}

/* boot everything once the DOM is ready. we don't wait for pywebviewready
   for the clock (pure JS, no Python needed) or the chart canvas (it just needs
   the DOM). we DO wait for pywebviewready for the polling loops, but with a
   fallback timeout in case the event was already fired before our listener bound. */

function bootUiOnly() {
    // clock is pure JS, kick it off immediately
    tickClock();
    setInterval(tickClock, 50);
    // chart canvas doesn't need pywebview to initialize either
    setTimeout(initChart, 200);
}

function bootPolling() {
    tickSnapshot();
    setInterval(tickSnapshot, REFRESH_MS);
}

/* run the pure-UI boot as soon as the DOM is parsed */
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootUiOnly);
} else {
    bootUiOnly();
}

/* run the polling boot once pywebview is available.
   handle three cases:
   1. pywebview already exists (event fired before listener bound): start now
   2. event fires: start on event
   3. neither happens in 3 seconds: give up on polling, at least UI is alive */
if (window.pywebview && window.pywebview.api) {
    bootPolling();
} else {
    let bootedPolling = false;
    window.addEventListener("pywebviewready", () => {
        if (bootedPolling) return;
        bootedPolling = true;
        bootPolling();
    });
    setTimeout(() => {
        if (bootedPolling) return;
        if (window.pywebview && window.pywebview.api) {
            bootedPolling = true;
            bootPolling();
        } else {
            console.error("[ENACT] pywebview bridge never became available");
        }
    }, 3000);
}
</script>

</body>
</html>
"""


# entry point: opens the dashboard window and runs until the user closes it
def main() -> None:
    # inject Python-side constants into the JS so we have a single source of truth
    html = _DASHBOARD_HTML
    html = html.replace("CHART_REFRESH_MS_PLACEHOLDER", str(CHART_REFRESH_MS))
    html = html.replace("REFRESH_MS_PLACEHOLDER", str(REFRESH_MS))
    html = html.replace("TRACE_COLORS_PLACEHOLDER", json.dumps(TRACE_COLORS))
    html = html.replace("TRACE_TARGETS_PLACEHOLDER", json.dumps(TRACE_TARGETS))
    html = html.replace("TRACE_LABELS_PLACEHOLDER", json.dumps(TRACE_LABELS))

    api = DashboardAPI()
    window = webview.create_window(
        title="ENACT — Network Resilience Telemetry",
        html=html,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
        background_color="#000000",
    )
    # maximize on show: fills the monitor but keeps standard window chrome
    window.events.shown += lambda: window.maximize()
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()