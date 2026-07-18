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

from datetime import datetime
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
    
    # returns recent critical events for the incident log picker, so users
    # can reopen the incident window for a past event they've closed
    def get_recent_critical_events(self, limit: int = 30) -> list[dict]:
        rows = database.new_critical_events_since(-1)
        # newest first, cap to the requested limit
        rows.reverse()
        return rows[:limit]
    
    # generates a plaintext export of everything the user might want for
    # post-mortem review or bug reports. bounded in size, not a raw DB dump.
    # writes to a file the user picks via the pywebview save dialog
    def export_logs(self) -> dict:
        try:
            report = self._build_report()

            # ask pywebview to show a native "Save As..." dialog
            default_name = (
                f"enact-report-"
                f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            )
            windows = webview.windows
            if not windows:
                return {"ok": False, "error": "no window available"}
            path = windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=default_name,
                file_types=("Text file (*.txt)", "All files (*.*)"),
            )
            if not path:
                # user cancelled the dialog
                return {"ok": True, "cancelled": True}

            # pywebview may return a tuple depending on backend, normalize
            if isinstance(path, (list, tuple)):
                path = path[0]

            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            return {"ok": True, "path": str(path), "bytes": len(report)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # builds the plaintext report, bounded so it doesn't grow unbounded
    # even on long-running sessions. structured with a header, event log,
    # current metrics snapshot, and recent sample tails per collector
    def _build_report(self) -> str:
        from io import StringIO
        buf = StringIO()

        def section(title: str) -> None:
            buf.write("\n")
            buf.write("=" * 70 + "\n")
            buf.write(title + "\n")
            buf.write("=" * 70 + "\n")

        # header
        buf.write("ENACT · Engine for Network Anomaly, Condition, and Telemetry\n")
        buf.write("Diagnostic report\n")
        buf.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")

        # current state snapshot
        section("CURRENT STATUS")
        try:
            status = database.status_snapshot()
            for metric_name, entry in status.items():
                value = entry.get("value", "?")
                ts = entry.get("ts", "?")
                buf.write(f"  {metric_name:20s} = {str(value):20s} (as of {ts})\n")
        except Exception as e:
            buf.write(f"  (status snapshot failed: {e})\n")

        # collector health
        section("COLLECTOR HEALTH · MOST RECENT CYCLE")
        try:
            for row in database.latest_run_per_collector():
                buf.write(f"  {row['collector']:15s} "
                         f"status={row['status']:6s} "
                         f"dur={row['duration_ms']:>6.0f}ms "
                         f"samples={row['sample_count']} "
                         f"at {row['ts']}\n")
        except Exception as e:
            buf.write(f"  (collector health query failed: {e})\n")

        # current metric snapshots
        section("CURRENT METRIC READINGS")
        try:
            for row in database.latest_metric_snapshots():
                val = row["value"] if row["value"] is not None else row["value_str"]
                buf.write(f"  {row['collector']:15s} "
                         f"{row['metric']:25s} = {str(val):20s} "
                         f"({row['ts']})\n")
        except Exception as e:
            buf.write(f"  (metric snapshot query failed: {e})\n")

        # event log: last 200 events (bounded)
        section("EVENT LOG · MOST RECENT 200 EVENTS")
        try:
            events = database.recent_events(limit=200)
            if not events:
                buf.write("  (no events recorded)\n")
            for e in events:
                buf.write(f"[{e['ts']}] {e['severity'].upper():8s} "
                         f"{e['type']:20s} :: {e['summary']}\n")
        except Exception as e:
            buf.write(f"  (event log query failed: {e})\n")

        # recent samples tail per collector: last 50 rows each, so the report
        # gives context on what was happening without dumping everything
        section("RECENT SAMPLES · LAST 50 PER COLLECTOR")
        try:
            for collector in ["connectivity", "dns", "route",
                              "wifi", "status"]:
                buf.write(f"\n-- {collector.upper()} --\n")
                rows = database.recent_samples(collector, limit=50)
                if not rows:
                    buf.write("  (no recent samples)\n")
                    continue
                for r in rows:
                    val = r["value"] if r["value"] is not None else r["value_str"]
                    buf.write(f"  {r['ts']} {r['metric']:25s} = "
                             f"{str(val):15s}\n")
        except Exception as e:
            buf.write(f"  (recent samples query failed: {e})\n")

        section("END OF REPORT")
        return buf.getvalue()

    # opens the incident window in "test mode" without inserting anything into
    # the events table. this keeps the event log clean of synthetic data while
    # still letting users demo the full alarm-flash + incident-window UX.
    # the incident window itself handles the -1 sentinel and renders canned content
    def launch_test_incident(self) -> dict:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "src.dashboard.incident", "-1"],
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                              if sys.platform == "win32" else 0),
            )
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


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
    font-size: 23px;
}
#header .subtitle {
    color: var(--amber);
    font-weight: bold;
    letter-spacing: 1px;
    font-size: 23px;
}
#header .date {
    color: var(--cyan);
    margin-left: auto;
    font-size: 19px;
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
    justify-content: space-between;
    padding: 0 12px;
    font-size: 11px;
    letter-spacing: 1px;
    text-shadow: var(--glow-cyan);
}
#footer .footer-hint {
    text-shadow: var(--glow-cyan);
}
.footer-btn {
    background: transparent;
    color: var(--cyan);
    border: 2px solid var(--cyan);
    padding: 4px 14px;
    font-family: inherit;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
    cursor: pointer;
    transition: all 0.12s ease;
    text-shadow: var(--glow-cyan);
}
.footer-btn:hover {
    background: var(--red);
    color: black;
    border-color: var(--red);
    text-shadow: none;
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

/* alarm overlay: strobing EMERGENCY box centered over the dashboard.
   only the box itself flashes. no full-screen tint, no fade, no shake.
   the strobe is a hard on/off using CSS steps() timing, not a gradient. */
#alarm-overlay {
    position: fixed;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    pointer-events: none;
    background: transparent;
}
#alarm-overlay.hidden { display: none; }

/* the strobing box: this is the only element that animates. fills its
   viewport width mostly, EMERGENCY text fills the box top to bottom */
.alarm-strobe {
    background: #c00000;
    color: #ffffff;
    border: 6px solid #ff3030;
    padding: 24px 60px;
    font-weight: bold;
    font-size: 14vw;              /* scales with viewport, always huge */
    letter-spacing: 0.05em;
    line-height: 1;
    text-shadow: 0 0 24px rgba(255, 255, 255, 0.6),
                 0 0 8px rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 60px rgba(255, 0, 0, 0.5);
    /* discrete on/off strobe. total run: 6 iterations × 0.4s = 2.4s */
    animation: strobe-blink 0.3s steps(1, end) 4;
}
@keyframes strobe-blink {
    0%, 49%   { opacity: 1; visibility: visible; }
    50%, 100% { opacity: 0; visibility: hidden; }
}

/* info block below the strobing box: event summary and status. does NOT
   strobe — it stays static and readable throughout the alarm */
.alarm-info {
    margin-top: 22px;
    text-align: center;
    color: #ffbfbf;
    background: rgba(0, 0, 0, 0.75);
    padding: 14px 28px;
    border: 2px solid rgba(255, 48, 48, 0.6);
    max-width: 70vw;
    text-shadow: 0 0 8px rgba(255, 0, 0, 0.7);
}
.alarm-info .summary {
    font-size: 14px;
    letter-spacing: 1px;
    color: #ffffff;
    margin-bottom: 8px;
    line-height: 1.35;
}
.alarm-info .launch {
    font-size: 11px;
    letter-spacing: 3px;
    color: #ff8888;
}

/* small info button next to panel titles: click opens a popup explaining
   what the panel shows. styled like the incident window's ACK button */
.info-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    margin-left: 8px;
    background: transparent;
    color: var(--cyan);
    border: 1px solid var(--cyan);
    border-radius: 50%;
    font-family: inherit;
    font-size: 10px;
    font-weight: bold;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    vertical-align: middle;
    pointer-events: auto;
    transition: all 0.12s ease;
    text-shadow: var(--glow-cyan);
}
.info-btn:hover {
    background: var(--red);
    color: black;
    border-color: var(--red);
    text-shadow: none;
}

/* small button in a panel title bar, for actions like reopening incidents.
   same visual language as the acknowledged / info buttons */
.panel-title-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 1px 8px;
    margin-left: 8px;
    background: transparent;
    color: var(--red);
    border: 1px solid var(--red);
    font-family: inherit;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    cursor: pointer;
    pointer-events: auto;
    transition: all 0.12s ease;
    text-shadow: var(--glow-red);
    vertical-align: middle;
}
.panel-title-btn:hover {
    background: var(--red);
    color: black;
    text-shadow: none;
}

/* export button gets amber styling to distinguish from the red incident button.
   still uses the same "hover turns red" language as everything else */
.panel-title-btn.export-btn {
    color: var(--cyan);
    border-color: var(--cyan);
    text-shadow: var(--glow-cyan);
}
.panel-title-btn.export-btn:hover {
    background: var(--red);
    color: black;
    border-color: var(--red);
    text-shadow: none;
}

/* incident log popup: modal listing past critical events, click one to
   reopen its incident window */
#incident-log-popup {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.65);
    z-index: 850;
}
#incident-log-popup.hidden { display: none; }
#incident-log-popup .card {
    width: 720px;
    max-width: 82vw;
    max-height: 78vh;
    background: var(--bg-panel);
    border: 3px solid var(--red);
    padding: 22px 28px 18px;
    display: flex;
    flex-direction: column;
    box-shadow: 0 0 60px rgba(0, 0, 0, 0.9),
                0 0 24px rgba(255, 48, 48, 0.30);
}
#incident-log-popup .title {
    color: var(--red-bright);
    font-weight: bold;
    letter-spacing: 2px;
    font-size: 14px;
    margin-bottom: 4px;
    text-shadow: var(--glow-red);
}
#incident-log-popup .subtitle {
    color: var(--text-mute);
    font-size: 11px;
    letter-spacing: 1px;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255, 48, 48, 0.3);
    text-shadow: var(--glow-cyan);
}
#incident-log-popup .list {
    flex: 1;
    overflow-y: auto;
    min-height: 100px;
    margin-bottom: 14px;
}
#incident-log-popup .list::-webkit-scrollbar { width: 6px; }
#incident-log-popup .list::-webkit-scrollbar-thumb { background: rgba(255, 48, 48, 0.25); }
#incident-log-popup .incident-row {
    display: grid;
    grid-template-columns: 90px 130px 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 8px 10px;
    border: 1px solid rgba(255, 48, 48, 0.15);
    margin-bottom: 6px;
    cursor: pointer;
    transition: all 0.12s ease;
    text-shadow: var(--glow-red);
}
#incident-log-popup .incident-row:hover {
    background: rgba(255, 48, 48, 0.08);
    border-color: var(--red);
}
#incident-log-popup .incident-row .ts { color: var(--cyan); font-size: 11px; text-shadow: var(--glow-cyan); }
#incident-log-popup .incident-row .type { color: var(--amber); font-weight: bold; text-shadow: var(--glow-amber); }
#incident-log-popup .incident-row .summary { color: var(--red-bright); font-size: 12px; }
#incident-log-popup .incident-row .reopen { color: var(--red); font-size: 10px; letter-spacing: 1px; font-weight: bold; }
#incident-log-popup .empty {
    color: var(--text-mute);
    text-align: center;
    padding: 30px;
    letter-spacing: 2px;
    text-shadow: var(--glow-cyan);
}

/* centered popup card that appears when an info button is clicked.
   modal-style with a dimmed backdrop, click-outside dismisses */
#info-popup {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.55);
    z-index: 800;
}
#info-popup.hidden { display: none; }
#info-popup .card {
    width: 520px;
    max-width: 78vw;
    background: var(--bg-panel);
    border: 3px solid var(--amber);
    padding: 22px 28px 18px;
    color: var(--amber);
    box-shadow: 0 0 60px rgba(0, 0, 0, 0.9),
                0 0 24px rgba(215, 175, 0, 0.25);
}
#info-popup .title {
    color: var(--amber-bright);
    font-weight: bold;
    letter-spacing: 2px;
    font-size: 14px;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(0, 175, 255, 0.25);
    text-shadow: var(--glow-amber);
}
#info-popup .body {
    color: var(--amber);
    font-size: 12.5px;
    line-height: 1.55;
    margin-bottom: 18px;
}
#info-popup .btn-row {
    display: flex;
    justify-content: flex-end;
}

.info-close-btn {
    background: transparent;
    color: var(--amber);
    border: 2px solid var(--amber);
    padding: 6px 20px;
    font-family: inherit;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 2px;
    cursor: pointer;
    transition: all 0.15s ease;
    text-shadow: var(--glow-amber);
}
.info-close-btn:hover {
    background: var(--red);
    color: black;
    border-color: var(--red);
    text-shadow: none;
}

/* startup loading veil: hides the whole dashboard until fonts are loaded
   and the first data tick has fired. this prevents users from seeing the
   "wrong font before DSEG7 loads" flash of unstyled content */
#loading-veil {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.92);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    transition: opacity 0.55s ease-out, visibility 0.55s ease-out;
    opacity: 1;
    visibility: visible;
}
#loading-veil.gone {
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
}
#loading-veil .brand {
    color: var(--amber-bright);
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-weight: bold;
    font-size: 28px;
    letter-spacing: 8px;
    text-shadow: var(--glow-amber);
    margin-bottom: 14px;
}
#loading-veil .subtitle {
    color: var(--cyan-dim);
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 12px;
    letter-spacing: 3px;
    text-shadow: var(--glow-cyan);
    margin-bottom: 32px;
}
/* animated scanline: three dots that pulse in sequence for the boot animation.
   uses staggered animation-delay to create the traveling-dot effect */
#loading-veil .dots {
    display: flex;
    gap: 12px;
}
#loading-veil .dots span {
    width: 10px;
    height: 10px;
    background: var(--amber-bright);
    box-shadow: 0 0 12px rgba(215, 175, 0, 0.7);
    animation: veil-dot 1.1s ease-in-out infinite;
}
#loading-veil .dots span:nth-child(2) { animation-delay: 0.18s; }
#loading-veil .dots span:nth-child(3) { animation-delay: 0.36s; }
#loading-veil .dots span:nth-child(4) { animation-delay: 0.54s; }
#loading-veil .dots span:nth-child(5) { animation-delay: 0.72s; }
@keyframes veil-dot {
    0%, 100% { opacity: 0.15; transform: scale(0.8); }
    50%      { opacity: 1.0;  transform: scale(1.15); }
}
#loading-veil .status {
    margin-top: 32px;
    color: var(--text-mute);
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 10px;
    letter-spacing: 2px;
    text-shadow: var(--glow-cyan);
}

</style>
</head>
<body>

<!-- loading veil: covers the whole window until fonts have loaded and the
     first data snapshot has been rendered. fades out once ready -->
<div id="loading-veil">
    <div class="brand">[ ENACT ]</div>
    <div class="subtitle">NETWORK RESILIENCE TELEMETRY</div>
    <div class="dots">
        <span></span><span></span><span></span><span></span><span></span>
    </div>
    <div class="status">LOADING</div>
</div>

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
            <span class="panel-title">[ STATUS ] <button class="info-btn" data-info="status">i</button></span>
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
            <span class="panel-title">[ COLLECTOR HEALTH MONITOR ] <button class="info-btn" data-info="health">i</button></span>
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
            <span class="panel-title">[ TELEMETRY READOUT ] <button class="info-btn" data-info="metrics">i</button></span>
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
            <span class="panel-title">[ EVENT LOG ] <button class="info-btn" data-info="events">i</button> <button class="panel-title-btn" id="incident-log-btn">◆ INCIDENTS</button> <button class="panel-title-btn export-btn" id="export-logs-btn">⇩ EXPORT</button></span>
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
            <span class="panel-title">[ LATENCY TRACE · LIVE OSCILLOSCOPE ] <button class="info-btn" data-info="latency">i</button></span>
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

    <!-- footer with test-incident button on the left, hint text on the right -->
    <div id="footer">
        <button class="footer-btn" id="test-alarm-btn">TRIGGER TEST INCIDENT</button>
        <span class="footer-hint">PRESS ALT+F4 OR CLOSE WINDOW TO DISENGAGE</span>
    </div>

    <!-- alarm overlay: strobes the EMERGENCY word only, with static info below.
         no full-screen tint, no fade animation — hard on/off strobe -->
    <div id="alarm-overlay" class="hidden">
        <div class="alarm-strobe">EMERGENCY</div>
        <div class="alarm-info">
            <div class="summary" id="alarm-overlay-summary"></div>
            <div class="launch">LAUNCHING INCIDENT REPORT...</div>
        </div>
    </div>

    <!-- incident log popup: browsable history of past critical events.
         click any row to reopen its incident window -->
    <div id="incident-log-popup" class="hidden">
        <div class="card">
            <div class="title">◆ INCIDENT LOG</div>
            <div class="subtitle">RECENT CRITICAL EVENTS · CLICK TO REOPEN</div>
            <div class="list" id="incident-log-list"></div>
            <div class="btn-row">
                <button class="info-close-btn" id="incident-log-close">CLOSE</button>
            </div>
        </div>
    </div>

    <!-- info popup: opens when the user clicks an (i) button on a panel title.
         click outside or CLOSE to dismiss -->
    <div id="info-popup" class="hidden">
        <div class="card">
            <div class="title" id="info-popup-title">—</div>
            <div class="body" id="info-popup-body">—</div>
            <div class="btn-row">
                <button class="info-close-btn" id="info-popup-close">CLOSE</button>
            </div>
        </div>
    </div>

</div>

<script>
const TRACE_COLORS = TRACE_COLORS_PLACEHOLDER;
const TRACE_TARGETS = TRACE_TARGETS_PLACEHOLDER;
const TRACE_LABELS = TRACE_LABELS_PLACEHOLDER;
const REFRESH_MS = REFRESH_MS_PLACEHOLDER;
const CHART_REFRESH_MS = CHART_REFRESH_MS_PLACEHOLDER;

/* text shown in the info popup for each panel. click the (i) buttons to view */
const PANEL_INFO = {
    status: {
        title: "CONNECTIVITY STATUS",
        body: "Live state of your Wi-Fi association, internet reachability, and VPN tunnel. WI-FI reports the SSID you're associated with. INTERNET is a composite of DNS resolution and ICMP reachability — a 'DEGRADED' reading typically means ICMP is filtered by a firewall or VPN while DNS still works. VPN reports whether a tunnel adapter is currently active on your machine.",
    },
    health: {
        title: "COLLECTOR HEALTH MONITOR",
        body: "Reports the most recent cycle of each collector. UNIT is the collector name. LAST is how long ago it ran. STATUS is 'ok' if the cycle completed without error. DUR is how long the cycle took to run. SAMPLES is how many telemetry records it produced. If a status stays red or the LAST value grows unboundedly, that collector is unhealthy.",
    },
    metrics: {
        title: "TELEMETRY READOUT",
        body: "The most recent value of every metric being collected. SOURCE is which collector produced it, METRIC identifies what's being measured, VALUE is the latest reading, and AGE is how long ago it was captured. This is the raw telemetry that analyzers reason over.",
    },
    events: {
        title: "EVENT LOG",
        body: "Chronological log of anomalies detected by the analyzers. Severity is INFO (routine change), WARN (something notable), or CRIT (something wrong). Critical events also trigger a full-screen alarm and open a dedicated incident window. Boxed values in each summary are the diagnostic data — fingerprints, IPs, hop counts — so the eye can catch what actually changed at a glance.",
    },
    latency: {
        title: "LATENCY TRACE · LIVE OSCILLOSCOPE",
        body: "Live ping latency to three public DNS resolvers: Cloudflare, Google, and Quad9. Uses ICMP echo. If the chart shows 'NO DATA · ICMP BLOCKED OR UNREACHABLE', your network drops ping packets — common with VPNs and corporate firewalls — and this specific chart can't gather data. Other collectors (DNS resolution timing, route tracing) still work under those conditions.",
    },
};

/* the veil exists purely to hide the wrong-font transitional state until our
   web fonts finish loading. once fonts are ready, dismiss immediately —
   telemetry can populate afterwards with its own per-panel initializing
   messages, which are more diagnostically useful than a global veil */
function hideVeil() {
    const veil = document.getElementById("loading-veil");
    if (veil) veil.classList.add("gone");
}

function setVeilStatus(text) {
    const el = document.getElementById("loading-veil-status");
    if (el) el.textContent = text;
}

if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(hideVeil);
} else {
    // very old browsers without the fonts API, dismiss immediately
    hideVeil();
}

/* fallback: if fonts never load (blocked CDN, offline), give up waiting
   after 4 seconds and dismiss anyway. fallback Cascadia Mono is fine */
setTimeout(hideVeil, 4000);

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

/* wire the EXPORT button. requests a plaintext dump of everything relevant
   (events, current state, recent samples) from python, which prompts the
   user with a Save As dialog and writes to the chosen file */
function initExportButton() {
    const btn = document.getElementById("export-logs-btn");
    if (!btn) return;
    btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = "GENERATING...";
        try {
            const result = await window.pywebview.api.export_logs();
            if (result && result.ok) {
                if (result.cancelled) {
                    btn.textContent = originalText;
                } else {
                    btn.textContent = "SAVED";
                    setTimeout(() => { btn.textContent = originalText; }, 1600);
                }
            } else {
                btn.textContent = "EXPORT FAILED";
                setTimeout(() => { btn.textContent = originalText; }, 2000);
            }
        } catch (err) {
            btn.textContent = "EXPORT FAILED";
            setTimeout(() => { btn.textContent = originalText; }, 2000);
        }
        btn.disabled = false;
    });
}

/* format a numeric value compactly: floats get one decimal, ints stay whole */
function formatValue(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
        return Number.isInteger(v) ? v.toString() : v.toFixed(1);
    }
    return String(v);
}

/* formats an ISO timestamp as MM-DD HH:MM for the incident log rows */
function formatCompactTime(isoTs) {
    const d = new Date(isoTs);
    if (isNaN(d)) return "?";
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
}

/* wire the INCIDENTS button in the event log panel. opens a modal listing
   past critical events, click any row to relaunch its incident window */
function initIncidentLogButton() {
    const btn = document.getElementById("incident-log-btn");
    if (!btn) return;
    const popup = document.getElementById("incident-log-popup");
    const list = document.getElementById("incident-log-list");

    btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        // fetch the list of past critical events fresh on each open
        try {
            const events = await window.pywebview.api.get_recent_critical_events(30);
            renderIncidentList(events);
        } catch (err) {
            list.innerHTML = `<div class="empty">FAILED TO LOAD INCIDENT HISTORY</div>`;
        }
        popup.classList.remove("hidden");
    });
    document.getElementById("incident-log-close").addEventListener("click", () => {
        popup.classList.add("hidden");
    });
    popup.addEventListener("click", (e) => {
        if (e.target.id === "incident-log-popup") popup.classList.add("hidden");
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") popup.classList.add("hidden");
    });
}

/* renders the incident log list, with each row a click-to-reopen button */
function renderIncidentList(events) {
    const list = document.getElementById("incident-log-list");
    if (!events || events.length === 0) {
        list.innerHTML = `<div class="empty">NO CRITICAL EVENTS RECORDED YET</div>`;
        return;
    }
    list.innerHTML = events.map(ev => `
        <div class="incident-row" data-event-id="${ev.id}">
            <div class="ts">${formatCompactTime(ev.ts)}</div>
            <div class="type">${escapeHtml((ev.type || "").toUpperCase())}</div>
            <div class="summary">${escapeHtml(ev.summary || "")}</div>
            <div class="reopen">REOPEN ▶</div>
        </div>
    `).join("");

    // wire click handlers
    list.querySelectorAll(".incident-row").forEach(row => {
        row.addEventListener("click", () => {
            const eventId = parseInt(row.dataset.eventId, 10);
            if (isNaN(eventId)) return;
            try {
                window.pywebview.api.launch_incident_window(eventId);
            } catch (e) { /* ignore */ }
            document.getElementById("incident-log-popup").classList.add("hidden");
        });
    });
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

    // internet: honest TCP-probe result. no more "degraded" ambiguity —
    // either the probe reaches the public internet or it doesn't
    const inet = status.internet_status;
    const inetBox = document.getElementById("status-internet");
    if (inet) {
        const map = {
            "ok":   { cls: "ok",  label: "ONLINE",  sub: "TCP probe reached public internet" },
            "down": { cls: "bad", label: "OFFLINE", sub: "TCP probes to 1.1.1.1 and 8.8.8.8 failed" },
        };
        const info = map[inet.value] || { cls: "na", label: "UNKNOWN", sub: inet.value };
        inetBox.className = `status-box ${info.cls}`;
        inetBox.querySelector(".value").textContent = info.label;
        inetBox.querySelector(".sub").textContent = info.sub;
    }

    // vpn: honest active-probe result. distinguishes "adapter says up but
    // tunnel actually broken" from real tunneled state, which was the whole
    // point of the redesign
    const vpn = status.vpn_status;
    const vpnBox = document.getElementById("status-vpn");
    if (vpn) {
        const map = {
            "connected":  { cls: "ok", label: "TUNNELED",
                            sub: "tunnel active, target reachable via VPN" },
            "not_needed": { cls: "ok", label: "DIRECT",
                            sub: "target reachable without tunnel" },
            "broken":     { cls: "bad", label: "BROKEN",
                            sub: "adapter up but tunnel not delivering" },
            "none":       { cls: "na", label: "NOT ACTIVE",
                            sub: "no tunnel active" },
            "unknown":    { cls: "na", label: "UNKNOWN",
                            sub: "no internet, VPN state indeterminable" },
        };
        const info = map[vpn.value] || { cls: "na", label: "UNKNOWN", sub: vpn.value };
        vpnBox.className = `status-box ${info.cls}`;
        vpnBox.querySelector(".value").textContent = info.label;
        vpnBox.querySelector(".sub").textContent = info.sub;
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

async function tickSnapshot() {
    try {
        const snap = await window.pywebview.api.get_snapshot();

        // each renderer wrapped so one throwing doesn't stop the others.
        // this is defensive: individual renderers may fail on unexpected
        // data shapes, but the dashboard should stay useful in aggregate
        try { renderStatus(snap.status); }        catch (e) { console.error("renderStatus:", e); }
        try { renderHealth(snap.collector_health); } catch (e) { console.error("renderHealth:", e); }
        try { renderMetrics(snap.current_metrics); } catch (e) { console.error("renderMetrics:", e); }
        try { renderEvents(snap.events); }        catch (e) { console.error("renderEvents:", e); }
        try { classifyChartState(snap); }         catch (e) { console.error("classify:", e); }
    } catch (e) {
        console.error("tickSnapshot fetch failed:", e);
    }
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

/* wire up the (i) buttons on each panel title. click opens the info popup
   with content from PANEL_INFO; click outside or CLOSE dismisses */
function initInfoButtons() {
    const popup = document.getElementById("info-popup");
    const titleEl = document.getElementById("info-popup-title");
    const bodyEl = document.getElementById("info-popup-body");

    document.querySelectorAll(".info-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const key = btn.dataset.info;
            const info = PANEL_INFO[key];
            if (!info) return;
            titleEl.textContent = info.title;
            bodyEl.textContent = info.body;
            popup.classList.remove("hidden");
        });
    });
    document.getElementById("info-popup-close").addEventListener("click", () => {
        popup.classList.add("hidden");
    });
    // click on the backdrop (not the card) to dismiss
    popup.addEventListener("click", (e) => {
        if (e.target.id === "info-popup") popup.classList.add("hidden");
    });
    // ESC also dismisses
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") popup.classList.add("hidden");
    });
}

/* wire the TRIGGER TEST INCIDENT button. fires the strobe flash locally and
   launches the incident window with a sentinel event id, without touching
   the events table. an in-flight flag prevents duplicate launches from
   fast double-clicks or focus/keyboard dispatch races */
let testAlarmInFlight = false;

function initTestAlarmButton() {
    const btn = document.getElementById("test-alarm-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
        // hard guard: if a test launch is already running, ignore additional
        // clicks. btn.disabled alone isn't enough because focus/keyboard events
        // can slip through before the first async iteration completes
        if (testAlarmInFlight) return;
        testAlarmInFlight = true;
        btn.disabled = true;
        btn.textContent = "TRIGGERING...";

        // pass launchIncident=false because we launch the test incident
        // ourselves right after this via launch_test_incident(). without this,
        // triggerAlarm would ALSO try to launch, giving us two windows
        triggerAlarm({
            id: -1,
            type: "test_incident",
            summary: "TEST · Synthetic incident from dashboard button",
        }, false);

        try {
            const result = await window.pywebview.api.launch_test_incident();
            if (!result || !result.ok) {
                btn.textContent = "TEST FAILED";
            }
        } catch (e) { /* ignore, alarm still fires locally */ }

        // 3-second cooldown before the button can be pressed again. long
        // enough that the incident window is fully open, short enough that
        // impatient testing still feels responsive
        setTimeout(() => {
            btn.textContent = "TRIGGER TEST INCIDENT";
            btn.disabled = false;
            testAlarmInFlight = false;
        }, 3000);
    });
}

function bootUiOnly() {
    tickClock();
    setInterval(tickClock, 50);
    setTimeout(initChart, 200);
    initInfoButtons();  
    initTestAlarmButton();
    initIncidentLogButton(); 
    initExportButton();
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

/* shows the strobing alarm overlay for the event, and optionally launches
   the associated incident window mid-flash. the launchIncident parameter
   lets callers who launch the incident window themselves (e.g. the test
   button) skip the built-in launch, preventing duplicate windows */
async function triggerAlarm(event, launchIncident = true) {
    if (alarmShowing) return;
    alarmShowing = true;

    const overlay = document.getElementById("alarm-overlay");
    document.getElementById("alarm-overlay-summary").textContent =
        `${event.type.toUpperCase()} · ${event.summary}`;
    overlay.classList.remove("hidden");
    const strobe = overlay.querySelector(".alarm-strobe");
    strobe.style.animation = "none";
    void strobe.offsetWidth;
    strobe.style.animation = "";

    if (launchIncident) {
        // launch the incident window mid-flash so it appears just as the flash ends
        setTimeout(() => {
            window.pywebview.api.launch_incident_window(event.id);
        }, 1500);
    }

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