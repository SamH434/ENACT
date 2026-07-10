"""
Live terminal dashboard for ENACT, built with rich.

Run with:
    python -m src.dashboard.live
"""

import json
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box

from src.storage import database


# refresh fast enough that the clock ticks every second visibly
REFRESH_HZ = 20
LATENCY_TARGET = "1.1.1.1"
SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"

# color palette
COLOR_AMBER = "color(214)"        # primary text, amber/orange
COLOR_AMBER_BRIGHT = "color(220)" # highlights, bright amber
COLOR_CYAN = "color(51)"          # secondary data, gridlines
COLOR_CYAN_DIM = "color(37)"      # subdued grid elements
COLOR_RED = "color(196)"          # critical escalation only
COLOR_RED_DIM = "color(124)"      # red accents, less aggressive
COLOR_GREEN_DIM = "color(34)"     # "ok" status, dim so it doesn't dominate
COLOR_BG_BLACK = "black"
CRITICAL_WINDOW_SEC = 60


def _live_clock() -> str:
    """Local system clock. Uses datetime.now(), no network or external calls."""
    now = datetime.now()

    ms = f"{now.microsecond // 1000:03d}"
    return f"{now.strftime('%H:%M:%S')}.{ms}"


def _live_date() -> str:
    return datetime.now().strftime("%Y.%m.%d")

def _ago(iso_ts: str) -> str:
    try:
        then = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return "?"
    delta = datetime.now(timezone.utc) - then
    sec = int(delta.total_seconds())
    if sec < 0:
        return "now"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"

def _sparkline(values: list[float], width: int = 60) -> str:
    if not values:
        return "(no data)"
    if len(values) > width:
        bucket = len(values) / width
        sampled = []
        for i in range(width):
            start = int(i * bucket)
            end = int((i + 1) * bucket)
            chunk = values[start:end] if end > start else [values[start]]
            sampled.append(sum(chunk) / len(chunk))
        values = sampled

    lo = min(values)
    hi = max(values)
    if hi == lo:
        return SPARKLINE_CHARS[len(SPARKLINE_CHARS) // 2] * len(values)

    span = hi - lo
    chars = []
    for v in values:
        idx = int((v - lo) / span * (len(SPARKLINE_CHARS) - 1))
        chars.append(SPARKLINE_CHARS[idx])
    return "".join(chars)

def _bar_chart(values: list[float], width: int = 60, height: int = 8) -> list[str]:
    """Vertical block-character bar chart, returned as a list of row strings."""
    if not values:
        return [""] * height

    # downsample to chart width by bucketing, same trick as the sparkline
    if len(values) > width:
        bucket = len(values) / width
        sampled = []
        for i in range(width):
            start = int(i * bucket)
            end = int((i + 1) * bucket)
            chunk = values[start:end] if end > start else [values[start]]
            sampled.append(sum(chunk) / len(chunk))
        values = sampled
    else:
        pad = [None] * (width - len(values))
        values = pad + values

    real_values = [v for v in values if v is not None]
    if not real_values:
        return [""] * height
    lo = min(real_values)
    hi = max(real_values)

    if hi == lo:
        steps = [(height * 8) // 2 if v is not None else 0 for v in values]
    else:
        span = hi - lo
        steps = []
        for v in values:
            if v is None:
                steps.append(0)
            else:
                # normalize into 0 .. (height*8 - 1)
                s = int((v - lo) / span * (height * 8 - 1))
                steps.append(max(0, s))

    # render row by row, top to bottom
    rows = []
    for row_idx in range(height):
        # rows are numbered top-down, so row 0 is the highest part of the chart
        row_from_bottom = (height - 1) - row_idx
        cells = []
        for s in steps:
            # how many 8ths of this column does this row contain?
            row_floor = row_from_bottom * 8
            in_this_row = s - row_floor
            if in_this_row >= 8:
                cells.append(SPARKLINE_CHARS[-1])  # full block
            elif in_this_row <= 0:
                cells.append(" ")                  # empty cell
            else:
                cells.append(SPARKLINE_CHARS[in_this_row - 1])
        rows.append("".join(cells))
    return rows


# checks whether any critical event fired recently, drives EMERGENCY mode
def _is_critical_active() -> tuple[bool, str | None]:
    """Returns (is_critical, summary). summary is the most recent critical event's text."""
    rows = database.recent_events(limit=10)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=CRITICAL_WINDOW_SEC)
    for r in rows:
        if r["severity"] != "critical":
            continue
        try:
            event_ts = datetime.fromisoformat(r["ts"])
        except (ValueError, TypeError):
            continue
        if event_ts >= cutoff:
            return True, r["summary"]
    return False, None


# builds the title header bar, no longer carries the clock (which now has its own panel)
def _header_panel(critical_active: bool) -> Panel:
    date = _live_date()

    if critical_active:
        title = Text()
        title.append("ENACT", style=f"bold {COLOR_BG_BLACK} on {COLOR_RED}")
        title.append("  CODE: 102  ·  EMERGENCY  ", style=f"bold {COLOR_RED} on {COLOR_BG_BLACK}")
        title.append(f"  {date}  ", style=f"{COLOR_RED_DIM}")
        border = COLOR_RED
    else:
        title = Text()
        #title.append("ENACT", style=f"bold {COLOR_BG_BLACK} on {COLOR_AMBER_BRIGHT}")
        title.append("  [ ENGINE FOR NETWORK ANOMALY CONDITION AND TELEMETRY ]  ", style=f"bold {COLOR_AMBER}")
        title.append(f"  {date}  ", style=COLOR_CYAN)
        border = COLOR_AMBER

    return Panel(
        Align.center(title, vertical="middle"),
        border_style=border,
        box=box.HEAVY,
        height=3,
    )


# builds the dedicated clock panel
def _clock_panel(critical_active: bool) -> Panel:
    clock = _live_clock()

    main_time, ms = clock.rsplit(".", 1)

    color_main = COLOR_RED if critical_active else COLOR_AMBER_BRIGHT
    color_ms = COLOR_RED_DIM if critical_active else COLOR_AMBER
    color_label = COLOR_RED_DIM if critical_active else COLOR_CYAN_DIM
    border = COLOR_RED if critical_active else COLOR_AMBER

    body = Text()
    body.append("ACTIVE TIME DISPLAY\n", style=color_label)
    body.append(main_time, style=f"bold {color_main}")
    body.append(f".{ms}", style=color_ms)

    return Panel(
        Align.center(body, vertical="middle"),
        border_style=border,
        box=box.HEAVY,
        height=5,
    )

# builds the footer hint bar
def _footer_panel(critical_active: bool) -> Panel:
    if critical_active:
        text = Text(
            "  CAUTION · CAUTION · CAUTION · "
            "ANOMALY ACTIVE · INSPECT EVENT LOG · "
            "CAUTION · CAUTION · CAUTION  ",
            style=f"bold {COLOR_RED}",
            justify="center",
        )
        border = COLOR_RED
    else:
        text = Text(
            "  PRESS CTRL+C TO DISENGAGE  ",
            style=f"{COLOR_CYAN_DIM}",
            justify="center",
        )
        border = COLOR_CYAN_DIM
    return Panel(text, border_style=border, height=3)


# builds the collector health panel: which collectors are alive and well
def _collector_health_panel() -> Panel:
    rows = database.latest_run_per_collector()
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style=f"bold {COLOR_AMBER_BRIGHT}",
        expand=True,
        border_style=COLOR_CYAN_DIM,
    )
    table.add_column("UNIT", style=COLOR_CYAN, no_wrap=True)
    table.add_column("LAST", style=COLOR_AMBER)
    table.add_column("STATUS")
    table.add_column("DUR", style=COLOR_AMBER, justify="right")
    table.add_column("SAMPLES", style=COLOR_AMBER, justify="right")

    if not rows:
        table.add_row("(no telemetry recorded yet)", "", "", "", "")
    else:
        for r in rows:
            status_lower = r["status"]
            if status_lower == "ok":
                status_text = Text("● NORMAL", style=COLOR_GREEN_DIM)
            else:
                status_text = Text("● ERROR", style=f"bold {COLOR_RED}")
            duration = f"{r['duration_ms']:.0f}ms" if r["duration_ms"] else "?"
            table.add_row(
                r["collector"].upper(),
                _ago(r["ts"]),
                status_text,
                duration,
                str(r["sample_count"] or 0),
            )

    return Panel(
        table,
        title=f"[ COLLECTOR HEALTH MONITOR ]",
        title_align="left",
        border_style=COLOR_AMBER,
        box=box.HEAVY,
    )


# builds the telemetry readout: most recent value of every (collector, metric)
def _current_network_panel() -> Panel:
    rows = database.latest_metric_snapshots()
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style=f"bold {COLOR_AMBER_BRIGHT}",
        expand=True,
        border_style=COLOR_CYAN_DIM,
    )
    table.add_column("SOURCE", style=COLOR_CYAN, no_wrap=True)
    table.add_column("METRIC", style=COLOR_CYAN)
    table.add_column("VALUE", style=f"bold {COLOR_AMBER_BRIGHT}", justify="right")
    table.add_column("AGE", style=COLOR_AMBER, justify="right")

    if not rows:
        table.add_row("(no samples yet)", "", "", "")
    else:
        for r in rows:
            val_display = r["value"] if r["value"] is not None else r["value_str"]
            if val_display is None:
                val_display = "—"
            if isinstance(val_display, float):
                val_display = f"{val_display:.1f}"
            else:
                val_display = str(val_display)
            table.add_row(
                r["collector"].upper(),
                r["metric"],
                val_display,
                _ago(r["ts"]),
            )

    return Panel(
        table,
        title=f"[ TELEMETRY READOUT ]",
        title_align="left",
        border_style=COLOR_AMBER,
        box=box.HEAVY,
    )


# builds the event log: severity colored, newest first, with hatched border on critical
def _events_panel(critical_active: bool) -> Panel:
    rows = database.recent_events(limit=10)
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style=f"bold {COLOR_AMBER_BRIGHT}",
        expand=True,
        border_style=COLOR_CYAN_DIM,
    )
    table.add_column("AGE", style=COLOR_CYAN, no_wrap=True)
    table.add_column("SEV", no_wrap=True)
    table.add_column("TYPE", style=COLOR_AMBER, no_wrap=True)
    table.add_column("SUMMARY")

    if not rows:
        table.add_row("(none)", "", "", Text("no events in log", style=COLOR_CYAN_DIM))
    else:
        for r in rows:
            sev = r["severity"]
            if sev == "critical":
                sev_text = Text("◆ CRIT", style=f"bold {COLOR_RED}")
                summary_style = COLOR_RED
            elif sev == "warning":
                sev_text = Text("▲ WARN", style=f"bold {COLOR_AMBER_BRIGHT}")
                summary_style = COLOR_AMBER
            else:
                sev_text = Text("● INFO", style=COLOR_CYAN)
                summary_style = COLOR_CYAN
            table.add_row(
                _ago(r["ts"]),
                sev_text,
                r["type"],
                Text(r["summary"], style=summary_style),
            )

    border = COLOR_RED if critical_active else COLOR_AMBER
    title_prefix = "[ EVENT LOG · ALARM ]" if critical_active else "[ EVENT LOG · PHASE 4 ]"
    return Panel(
        table,
        title=title_prefix,
        title_align="left",
        border_style=border,
        box=box.HEAVY,
    )


# composes the full layout for one render frame
def _build_layout() -> Layout:
    critical_active, _ = _is_critical_active()

    layout = Layout()
    # outer column: title row (with clock to the right), then body, then footer
    layout.split_column(
        Layout(name="topbar", size=5),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    # top bar: title panel on the left, clock panel on the right
    layout["topbar"].split_row(
        Layout(_header_panel(critical_active), name="title", ratio=3),
        Layout(_clock_panel(critical_active), name="clock", ratio=1),
    )

    layout["footer"].update(_footer_panel(critical_active))

    # 2x2 body grid
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(_collector_health_panel(), name="health"),
        Layout(_events_panel(critical_active), name="events"),
    )
    layout["right"].split_column(
        Layout(_current_network_panel(), name="current"),
        Layout(_sparkline_panel(), name="sparkline"),
    )

    return layout


def main() -> None:
    console = Console()
    try:
        with Live(_build_layout(), refresh_per_second=REFRESH_HZ,
                  console=console, screen=True) as live:
            while True:
                live.update(_build_layout())
    except KeyboardInterrupt:
        console.print("\n[dim]dashboard disengaged[/dim]")

def render_html_frame(width: int = 150, height: int = 42) -> str:
    """Build one dashboard frame and export it as standalone HTML."""
    import io
    # critical: pipe console output to an in-memory buffer so frames don't
    # also flood the real terminal. without this, the dashboard's 20fps render
    # loop dumps ascii into vscode's terminal, freezing the main thread and
    # making the pywebview window unclickable.
    sink = io.StringIO()
    console = Console(
        record=True,
        width=width,
        height=height,
        color_system="truecolor",
        force_terminal=True,
        file=sink,
    )
    console.print(_build_layout())
    return console.export_html(inline_styles=True, code_format=HTML_TEMPLATE)

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>ENACT</title>
<style>
html, body {{
    margin: 0;
    padding: 0;
    background: #000000;
    color: #d7af00;
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    overflow: hidden;
    height: 100vh;
}}
pre {{
    margin: 0;
    padding: 12px;
    font-size: 13px;
    line-height: 1.15;
    white-space: pre;
    overflow: hidden;
}}
</style>
</head>
<body>
<pre style="font-family: inherit">{code}</pre>
</body>
</html>
"""

if __name__ == "__main__":
    main()