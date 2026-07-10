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
import subprocess
import sys
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

    # polled by the main dashboard's alarm watcher, returns any new critical
    # events since the last known id. the client tracks the watermark itself
    def get_new_criticals(self, since_id: int) -> list[dict]:
        return database.new_critical_events_since(since_id)

    # spawns the incident window as its own subprocess so it lives on its own
    # event loop, independent of this dashboard. returns True on launch, False
    # on failure. failure is not fatal for the main dashboard
    def launch_incident_window(self, event_id: int) -> bool:
        try:
            # use the same Python interpreter that's running this dashboard
            subprocess.Popen(
                [sys.executable, "-m", "src.dashboard.incident", str(event_id)],
                # detach on Windows so the child survives if we close later
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                              if sys.platform == "win32" else 0),
            )
            return True
        except Exception as e:
            print(f"[ENACT] failed to launch incident window: {e}")
            return False



# the static HTML/CSS/JS that drives the dashboard. python only provides data,
# the browser does layout and rendering. constants get injected at startup
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>ENACT</title>
<!-- DSEG7-Classic: authentic seven-segment LCD font for the clock readout.
     falls back to MingLiU_HKSCS-ExtB automatically if the CDN is unreachable -->
<style>
@font-face {
    font-family: 'MingLiU_HKSCS-ExtB';
    src: url('https://cdn.jsdelivr.net/gh/keshikan/DSEG@master/fonts/DSEG7-Classic/DSEG7Classic-Bold.woff2') format('woff2');
    font-weight: bold;
    font-style: normal;
    font-display: swap;
}
</style>
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
    --red-bright: #ff5050;
    --red-dim: #b04040;
    --red-mute: rgba(255, 48, 48, 0.15);
    --green-dim: #5fcf5f;
    --yellow: #ffd700;
    --text-mute: #5a7e8a;

    /* text glow variants: subtle color-halo behind letters for the
       "dimly lit control room" aesthetic. add via text-shadow */
    --glow-amber:  0 0 6px rgba(215, 175, 0, 0.45);
    --glow-cyan:   0 0 6px rgba(0, 175, 255, 0.45);
    --glow-red:    0 0 8px rgba(255, 48, 48, 0.55);
    --glow-green:  0 0 6px rgba(95, 207, 95, 0.45);
    --glow-yellow: 0 0 6px rgba(255, 215, 0, 0.45);
    --glow-white:  0 0 6px rgba(255, 255, 255, 0.35);
}

* { box-sizing: border-box; }

html, body {
    margin: 0;
    padding: 0;
    height: 100vh;
    background: var(--bg);
    color: var(--amber);
    font-family: 'MingLiU_HKSCS-ExtB', 'Consolas', 'Courier New', monospace;
    font-size: 13px;
    overflow: hidden;
    text-shadow: var(--glow-amber);

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
    grid-template-columns: 220px 1fr 1fr;
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
    gap: 18px;
}
#header .label {
    background: var(--amber-bright);
    color: var(--bg);
    font-weight: bold;
    padding: 4px 8px;
    letter-spacing: 0.5px;
    font-size: 30px;
}
#header .subtitle {
    color: var(--amber);
    font-weight: bold;
    letter-spacing: 1px;
    font-size: 24px;
}
#header .date {
    color: var(--cyan);
    margin-left: auto;
    font-size: 20px;
}

/* clock panel: large numeric readout, label above */
#clock {
    border: 2px solid var(--amber);
    background: var(--bg-panel);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 6px 12px;
    overflow: hidden;
}
#clock .label {
    color: var(--text-mute);
    font-size: 10px;
    letter-spacing: 2px;
    margin-bottom: 2px;
    text-shadow: var(--glow-cyan);
}
/* the numeric readout wrapper keeps both parts (HH:MM:SS and .mmm) on the
   same baseline at the same size, so nothing looks off-scale */
#clock .readout {
    display: flex;
    align-items: baseline;
    justify-content: center;
    line-height: 1;
    color: var(--amber-bright);
    /* DSEG7 primary, MingLiU_HKSCS-ExtB fallback if the CDN doesn't load */
    font-family: 'MingLiU_HKSCS-ExtB', 'MingLiU_HKSCS-ExtB', 'Consolas', monospace;
    font-weight: bold;
    font-size: 42px;         /* fills the box vertically */
    letter-spacing: 2px;
    text-shadow: var(--glow-amber);
}
#clock .readout .ms {
    color: var(--amber);
    font-size: 42px;         /* same size as time, differentiated only by color */
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
/* per-color glow overrides: text-shadow should match the text color, not
   inherit body-level amber, otherwise cyan text sits inside an amber halo */
td.source, td.age    { text-shadow: var(--glow-cyan); }
td.value             { text-shadow: var(--glow-amber); }
.status-ok           { text-shadow: var(--glow-green); }
.status-error        { text-shadow: var(--glow-red); }
.sev-info            { text-shadow: var(--glow-cyan); }
.sev-warning         { text-shadow: var(--glow-yellow); }
.sev-critical        { text-shadow: var(--glow-red); }
.summary-info        { text-shadow: var(--glow-cyan); }
.summary-warning     { text-shadow: var(--glow-amber); }
.summary-critical    { text-shadow: var(--glow-red); }

/* value boxes in event summaries get a slight glow too, keyed by class */
.val-box.val-hash    { text-shadow: var(--glow-cyan); }
.val-box.val-ip,
.val-box.val-num     { text-shadow: var(--glow-amber); }
.val-arrow           { text-shadow: var(--glow-cyan); }
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

/* full-panel overlay shown when the chart has no data to draw. sits on top of
   the (correctly empty) canvas and explains WHY no lines are visible, which is
   more useful than a silent black rectangle */
#chart-nodata {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    background: rgba(0, 0, 0, 0.55);
    pointer-events: none;
    z-index: 5;
    letter-spacing: 3px;
}
#chart-nodata.hidden { display: none; }
#chart-nodata .headline {
    color: var(--red);
    font-weight: bold;
    font-size: 42px;
    letter-spacing: 6px;
    text-shadow: 0 0 12px rgba(255, 48, 48, 0.4);
    margin-bottom: 12px;
}
#chart-nodata .reason-label {
    color: var(--text-mute);
    font-size: 10px;
    letter-spacing: 3px;
    margin-bottom: 4px;
}
#chart-nodata .reason {
    color: var(--red-bright, #ff5050);
    font-size: 16px;
    font-weight: bold;
    letter-spacing: 2px;
}

/* variant: initializing (softer, cyan not red) — for the first few seconds
   after the dashboard opens, before we can classify state honestly */
#chart-nodata.initializing .headline {
    color: var(--cyan);
    text-shadow: 0 0 12px rgba(0, 175, 255, 0.4);
    font-size: 28px;
    letter-spacing: 4px;
}
#chart-nodata.initializing .reason { color: var(--cyan-dim); }

/* event log scrolls if too tall, with subtle scrollbar styling */
.scroll-area {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
}
.scroll-area::-webkit-scrollbar { width: 6px; }
.scroll-area::-webkit-scrollbar-thumb { background: var(--cyan-mute); }

/* connectivity status column: three vertical status boxes on the left of the
   body grid. spans both rows of the 2x2 to its right, so it's a tall column
   containing three roughly-equal-height status boxes stacked vertically */
#status-strip {
    grid-row: 1 / -1;              /* span all rows of #body */
    display: grid;
    grid-template-rows: 1fr 1fr 1fr;
    gap: 12px;
    padding: 18px 12px 12px;
    border: 2px solid var(--amber);
    background: var(--bg-panel);
    position: relative;
    min-height: 0;
}
.status-box {
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 8px 14px;
    border: 2px solid var(--cyan-dim);
    background: rgba(0, 175, 255, 0.03);
    transition: border-color 0.3s ease, background 0.3s ease;
}
.status-box .label {
    color: var(--text-mute);
    font-size: 10px;
    letter-spacing: 1.5px;
    font-weight: bold;
    margin-bottom: 4px;
}
.status-box .value {
    font-size: 20px;
    font-weight: bold;
    letter-spacing: 1px;
    color: var(--amber-bright);
    line-height: 1.1;
}
.status-box .sub {
    color: var(--text-mute);
    font-size: 11px;
    margin-top: 4px;
    letter-spacing: 0.5px;
}

.status-box.ok {
    border-color: var(--green-dim);
    background: rgba(95, 207, 95, 0.04);
}
.status-box.ok .value { color: var(--green-dim); }

.status-box.degraded {
    border-color: var(--yellow);
    background: rgba(255, 215, 0, 0.06);
}
.status-box.degraded .value { color: var(--yellow); }

.status-box.bad {
    border-color: var(--red);
    background: rgba(255, 48, 48, 0.06);
}
.status-box.bad .value { color: var(--red); }

.status-box.na {
    border-color: var(--cyan-dim);
}
.status-box.na .value { color: var(--cyan-dim); font-size: 16px; }

/* initial loading state, replaced after first poll */
.loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--cyan);
    letter-spacing: 1px;
}

/* alarm overlay: full-window red flash on critical events, then fades out */
#alarm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(180, 0, 0, 0.75);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    pointer-events: none;
    animation: alarm-flash 2.5s ease-out forwards;
}
#alarm-overlay.hidden { display: none; }
@keyframes alarm-flash {
    0%    { background: rgba(255, 0, 0, 0.0); }
    5%    { background: rgba(255, 30, 30, 0.85); }
    15%   { background: rgba(180, 0, 0, 0.60); }
    25%   { background: rgba(255, 30, 30, 0.85); }
    35%   { background: rgba(180, 0, 0, 0.60); }
    50%   { background: rgba(255, 30, 30, 0.75); }
    85%   { background: rgba(180, 0, 0, 0.50); }
    100%  { background: rgba(180, 0, 0, 0.0); }
}
.alarm-content {
    text-align: center;
    color: white;
    text-shadow: 0 0 20px black, 0 0 40px rgba(255, 0, 0, 0.8);
    padding: 40px 80px;
    border: 6px solid white;
    background: rgba(0, 0, 0, 0);
}
@keyframes alarm-shake {
    0%, 100% { transform: translate(0, 0); }
    25%      { transform: translate(-3px, 2px); }
    50%      { transform: translate(2px, -2px); }
    75%      { transform: translate(-2px, -1px); }
}
.alarm-title {
    font-size: 62px;
    font-weight: bold;
    letter-spacing: 8px;
    margin-bottom: 12px;
    color: #ffdddd;
}
.alarm-code {
    font-size: 20px;
    letter-spacing: 4px;
    color: #ffaaaa;
    margin-bottom: 20px;
}
.alarm-summary {
    font-size: 16px;
    color: white;
    max-width: 700px;
    margin: 0 auto 20px auto;
    line-height: 1.4;
}
.alarm-launch {
    font-size: 12px;
    letter-spacing: 3px;
    color: #ffaaaa;
    margin-top: 24px;
}

</style>
</head>
<body>

<div id="app">

    <!-- top bar: title + clock -->
    <div id="topbar">
        <div id="header">
            <span class="label">ENACT</span>
            <span class="subtitle">ENGINE FOR NETWORK ANOMALY CONDITION AND TELEMETRY</span>
            <span class="date" id="header-date"></span>
        </div>
        <div id="clock">
            <div class="label">ACTIVE TIME DISPLAY</div>
            <div class="readout">
                <span class="time" id="clock-time">00:00:00</span><span class="ms" id="clock-ms">.000</span>
            </div>
        </div>
    </div>

    <!-- body grid: status column on the left, 2x2 panels on the right -->
    <div id="body">

        <!-- connectivity status: three stacked boxes spanning both rows -->
        <div id="status-strip">
            <span class="panel-title">[ STATUS ]</span>
            <div class="status-box na" id="status-wifi">
                <div class="label">WI-FI</div>
                <div class="value">—</div>
                <div class="sub">initializing</div>
            </div>
            <div class="status-box na" id="status-internet">
                <div class="label">INTERNET</div>
                <div class="value">—</div>
                <div class="sub">initializing</div>
            </div>
            <div class="status-box na" id="status-vpn">
                <div class="label">VPN</div>
                <div class="value">—</div>
                <div class="sub">initializing</div>
            </div>
        </div>

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
                <!-- overlay: shown only when chart has no data to draw.
                     honest labels beat silent empty panels -->
                <div id="chart-nodata" class="initializing">
                    <div class="headline" id="chart-nodata-headline">INITIALIZING</div>
                    <div class="reason-label">STATUS</div>
                    <div class="reason" id="chart-nodata-reason">stabilizing...</div>
                </div>
            </div>
        </div>

    </div>

    <!-- footer -->
    <div id="footer">PRESS ALT+F4 OR CLOSE WINDOW TO DISENGAGE</div>

    <!-- alarm overlay: hidden by default, flashes over the whole window on
         critical events, then fades out and triggers the incident window -->
    <div id="alarm-overlay" class="hidden">
        <div class="alarm-content">
            <div class="alarm-title">⚠ EMERGENCY ⚠</div>
            <div class="alarm-code">CRITICAL EVENT DETECTED</div>
            <div class="alarm-summary" id="alarm-overlay-summary"></div>
            <div class="alarm-launch">LAUNCHING INCIDENT REPORT...</div>
        </div>
    </div>

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

/* short day and month abbreviations for the header date. arrays are indexed by
   getDay() and getMonth() return values, keeps the format compact and readable */
const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function tickClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, "0");
    const m = String(now.getMinutes()).padStart(2, "0");
    const s = String(now.getSeconds()).padStart(2, "0");
    const ms = String(now.getMilliseconds()).padStart(3, "0");
    document.getElementById("clock-time").textContent = `${h}:${m}:${s}`;
    document.getElementById("clock-ms").textContent = `.${ms}`;

    // format: "Tue, Jul. 7" — day abbr + comma + month abbr + period + day-of-month
    const dayName = DAY_ABBR[now.getDay()];
    const monthName = MONTH_ABBR[now.getMonth()];
    const dayOfMonth = now.getDate();
    document.getElementById("header-date").textContent =
        `${dayName}, ${monthName}. ${dayOfMonth}`;
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

/* renders the three status boxes at the top of the dashboard.
   maps each status metric's value to a color-coded box: ok = green,
   degraded = yellow, down/disconnected = red, none = cyan-dim */
function renderStatus(status) {
    if (!status) return;

    // wi-fi
    const wifi = status.wifi_status;
    const wifiBox = document.getElementById("status-wifi");
    if (wifi) {
        if (wifi.value === "connected") {
            const ssid = wifi.meta.ssid || "?";
            wifiBox.className = "status-box ok";
            wifiBox.querySelector(".value").textContent = "CONNECTED";
            wifiBox.querySelector(".sub").textContent = `SSID: ${ssid}`;
        } else if (wifi.value === "disconnected") {
            wifiBox.className = "status-box bad";
            wifiBox.querySelector(".value").textContent = "DISCONNECTED";
            wifiBox.querySelector(".sub").textContent = "no association";
        } else {
            wifiBox.className = "status-box na";
            wifiBox.querySelector(".value").textContent = "UNKNOWN";
            wifiBox.querySelector(".sub").textContent = wifi.value || "unavailable";
        }
    }

    // internet: four possible states, each honest about what's actually broken
    const inet = status.internet_status;
    const inetBox = document.getElementById("status-internet");
    if (inet) {
        const map = {
            "ok":       { cls: "ok",       label: "ONLINE",   sub: "DNS + ping ok" },
            "degraded": { cls: "degraded", label: "DEGRADED", sub: "ICMP blocked (DNS ok)" },
            "no_dns":   { cls: "degraded", label: "NO DNS",   sub: "IP reachable, DNS broken" },
            "down":     { cls: "bad",      label: "OFFLINE",  sub: "no DNS, no route" },
        };
        const info = map[inet.value] || { cls: "na", label: "UNKNOWN", sub: inet.value };
        inetBox.className = `status-box ${info.cls}`;
        inetBox.querySelector(".value").textContent = info.label;
        inetBox.querySelector(".sub").textContent = info.sub;
    }

    // vpn: connected or not. we deliberately don't try to identify the vendor
    const vpn = status.vpn_status;
    const vpnBox = document.getElementById("status-vpn");
    if (vpn) {
        if (vpn.value === "connected") {
            vpnBox.className = "status-box ok";
            vpnBox.querySelector(".value").textContent = "TUNNELED";
            const count = vpn.meta.count || 1;
            const first = (vpn.meta.adapters || [])[0];
            const detail = first
                ? `${count} adapter${count > 1 ? 's' : ''} · ${first.name}`
                : `${count} active adapter${count > 1 ? 's' : ''}`;
            vpnBox.querySelector(".sub").textContent = detail;
        } else if (vpn.value === "none") {
            vpnBox.className = "status-box na";
            vpnBox.querySelector(".value").textContent = "NOT ACTIVE";
            vpnBox.querySelector(".sub").textContent = "no tunnel detected";
        } else {
            vpnBox.className = "status-box na";
            vpnBox.querySelector(".value").textContent = "UNKNOWN";
            vpnBox.querySelector(".sub").textContent = vpn.value || "unavailable";
        }
    }
}

/* classifies the chart's data state and updates the overlay label accordingly.
   called on every snapshot tick so it reacts quickly to state changes.
   states (in priority order):
     - engine_offline: no collector cycles happening at all
     - stale_data:     last successful latency was too long ago
     - no_latency:     samples flowing but zero successful latencies (ICMP block etc.)
     - initializing:   dashboard just opened, waiting for first data
     - ok:             data is fresh, hide the overlay
*/
function classifyChartState(snapshot) {
    const overlay = document.getElementById("chart-nodata");
    const headline = document.getElementById("chart-nodata-headline");
    const reason = document.getElementById("chart-nodata-reason");

    // find the most recent connectivity latency reading in the snapshot metrics
    let latencyMetric = null;
    let packetLossMetric = null;
    for (const m of (snapshot.current_metrics || [])) {
        if (m.collector === "connectivity" && m.metric === "latency_ms") latencyMetric = m;
        if (m.collector === "connectivity" && m.metric === "packet_loss_pct") packetLossMetric = m;
    }

    // has any connectivity cycle happened recently? check collector_health
    let connectivityHealth = null;
    for (const h of (snapshot.collector_health || [])) {
        if (h.collector === "connectivity") connectivityHealth = h;
    }
    const connectivityCycled = connectivityHealth !== null;
    const connectivityAgeSec = connectivityHealth
        ? (Date.now() - new Date(connectivityHealth.ts).getTime()) / 1000
        : Infinity;

    // no connectivity collector runs = engine is dead or hasn't started
    if (!connectivityCycled) {
        overlay.className = "";
        headline.textContent = "NO DATA";
        reason.textContent = "ENGINE OFFLINE";
        return;
    }

    // connectivity is cycling but the last cycle was ages ago = something wrong
    if (connectivityAgeSec > 120) {
        overlay.className = "";
        headline.textContent = "NO DATA";
        reason.textContent = "STALE · " + Math.floor(connectivityAgeSec / 60) + "M AGO";
        return;
    }

    // cycles are happening. is there any successful latency reading?
    // "successful" means value is a real number (not null, since ping failures
    // produce null latency), AND the reading is fresh (less than 90s old)
    const latencyAgeSec = latencyMetric
        ? (Date.now() - new Date(latencyMetric.ts).getTime()) / 1000
        : Infinity;
    const latencyIsFresh = latencyMetric
        && latencyMetric.value !== null
        && latencyMetric.value !== undefined
        && latencyAgeSec < 90;

    if (!latencyIsFresh) {
        // pings are cycling but not succeeding. classify by symptom.
        const lossHigh = packetLossMetric && packetLossMetric.value >= 90;
        overlay.className = "";
        headline.textContent = "NO DATA";
        if (lossHigh) {
            // 100% packet loss with cycles still happening = ICMP blocked, or
            // a total outage. we can't distinguish from packet loss alone, so
            // give the honest ambiguous answer
            reason.textContent = "ICMP BLOCKED OR UNREACHABLE";
        } else {
            reason.textContent = "NO SUCCESSFUL LATENCY";
        }
        return;
    }

    // real fresh data exists, hide the overlay
    overlay.classList.add("hidden");
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
        renderStatus(snap.status);
        renderHealth(snap.collector_health);
        renderMetrics(snap.current_metrics);
        renderEvents(snap.events);
        classifyChartState(snap);
    } catch (e) {
        // pywebview bridge not ready yet, retry next tick
    }
}

let chart = null;

/* small Chart.js plugin that applies a colored shadow to each line dataset
   as it's drawn. gives the traces the same "dimly lit CRT" glow as the rest
   of the dashboard's text. the shadow color is taken from each dataset's own
   borderColor, so it self-matches per trace */
const lineGlowPlugin = {
    id: 'lineGlow',
    beforeDatasetDraw(chart, args) {
        const dataset = chart.data.datasets[args.index];
        const ctx = chart.ctx;
        ctx.save();
        ctx.shadowColor = dataset.borderColor;
        ctx.shadowBlur = 10;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
    },
    afterDatasetDraw(chart) {
        chart.ctx.restore();
    },
};

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
        plugins: [lineGlowPlugin],
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
                        font: { family: "MingLiU_HKSCS-ExtB, monospace", size: 11 },
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
                        font: { family: "MingLiU_HKSCS-ExtB, monospace", size: 10 },
                    },
                },
                y: {
                    beginAtZero: true,
                    // hard cap the axis so a single weird sample can't compress the
                    // whole chart to look flat. 200ms is well above healthy internet
                    // latency; anything higher genuinely IS an anomaly and shows as
                    // a clipped spike, which is the right visual signal
                    suggestedMax: 200,
                    grid: { color: "rgba(0, 175, 255, 0.10)" },
                    ticks: {
                        color: "#5a7e8a",
                        font: { family: "MingLiU_HKSCS-ExtB, monospace", size: 10 },
                        callback: v => v + "ms",
                    },
                    title: {
                        display: true,
                        text: "LATENCY",
                        color: "#5a7e8a",
                        font: { family: "MingLiU_HKSCS-ExtB, monospace", size: 10 },
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

/* watermark for the alarm system: only fire for events strictly newer than this.
   initialize to -1 so we don't re-alarm for pre-existing critical events on startup,
   we'll set it to the current max on first poll instead */
let lastAlarmedId = null;
let alarmShowing = false;

/* runs on the same cadence as tickSnapshot: if any new critical events exist,
   flash the alarm and launch the incident window */
async function tickAlarmWatcher() {
    try {
        // on first run, set our watermark to the current highest critical event id
        // so we don't fire alarms for events that existed before the dashboard opened
        if (lastAlarmedId === null) {
            const initial = await window.pywebview.api.get_new_criticals(-1);
            lastAlarmedId = initial.length > 0
                ? Math.max(...initial.map(e => e.id))
                : 0;
            return;
        }
        const news = await window.pywebview.api.get_new_criticals(lastAlarmedId);
        if (news.length === 0 || alarmShowing) return;

        // alarm on the OLDEST unseen critical, then advance the watermark
        // past ALL of them. otherwise a burst of criticals would spam alarms
        const target = news[0];
        lastAlarmedId = Math.max(...news.map(e => e.id));
        triggerAlarm(target);
    } catch (e) {
        // ignore transient errors
    }
}

/* shows the alarm overlay, waits for the flash animation, then launches
   the incident window and hides the overlay */
async function triggerAlarm(event) {
    if (alarmShowing) return;
    alarmShowing = true;

    const overlay = document.getElementById("alarm-overlay");
    document.getElementById("alarm-overlay-summary").textContent =
        `${event.type.toUpperCase()} · ${event.summary}`;
    overlay.classList.remove("hidden");
    // restart the CSS animation by re-triggering it
    overlay.style.animation = "none";
    void overlay.offsetWidth;  // force reflow
    overlay.style.animation = "";

    // launch the incident window mid-flash so it appears just as the flash ends
    setTimeout(() => {
        window.pywebview.api.launch_incident_window(event.id);
    }, 1500);

    // hide the overlay after animation completes
    setTimeout(() => {
        overlay.classList.add("hidden");
        alarmShowing = false;
    }, 2500);
}

function bootPolling() {
    tickSnapshot();
    setInterval(tickSnapshot, REFRESH_MS);

    // alarm watcher runs on its own timer, polling for new critical events
    tickAlarmWatcher();
    setInterval(tickAlarmWatcher, 2000);  // 2s cadence is plenty
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