<p align="left">
  <img src="docs/assets/ENACTLogoWeb.png" alt="ENACT logo" width="300">
</p>

<p align="left">
  <img src="https://github.com/SamH434/ENACT/actions/workflows/test.yml/badge.svg" alt="tests status">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey" alt="Windows 10/11">
</p>

# ENACT: Engine for Network Anomaly, Condition, and Telemetry

ENACT is a passive network observability platform for Windows. It runs six collectors, six analyzers, and a native dashboard that surfaces detected anomalies with cross-signal evidence as they fire.

The differentiator versus a naive network monitor is the correlation model. Collectors do not share a clock or synchronize their work: each cycle is tagged with a `run_id` and UTC timestamp, and analyzers correlate independent signals via time-window bucketing rather than forced synchronization. Every detected event carries context from every collector active in the same window, giving operators the evidence needed to reason about causality without watching four charts simultaneously.

## Screenshots

<p align="left">
  <img src="screenshots/dashboard.png" alt="ENACT dashboard" width="900">
</p>

<p align="left">
  <img src="screenshots/incident.png" alt="Incident response window" width="900">
</p>

## Quickstart

Requires Python 3.10+ on Windows 10 or 11.

**One-time install:**

```powershell
git clone https://github.com/SamH434/ENACT.git
cd ENACT
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\setup-shortcut.ps1
```

The last line creates a desktop shortcut with the ENACT logo pointing at the silent launcher. If PowerShell blocks the script with an execution-policy error, run once with a bypass:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-shortcut.ps1
```

**Running ENACT.** Two options depending on preference.

*Recommended for daily use.* Double-click the **ENACT** shortcut on your desktop. The engine and dashboard both start silently. No terminal window remains open. Session logs are written to `logs/engine-session.log` and `logs/dashboard-session.log` for inspection if anything goes wrong.

*Recommended for development.* Run the engine and dashboard in separate terminals so you can watch stdout live:

```powershell
# terminal 1
python main.py

# terminal 2
python -m src.dashboard.window
```

The `enact-dashboard.bat` file is a middle-ground option that starts both processes in a visible cmd window, useful if you want to see the launcher's output but not maintain two terminals.

## Architecture

Four strictly separated layers:

- **Collectors** produce uniformly-shaped `TelemetryRecord` objects into a shared SQLite store on independent intervals. Collectors do not talk to each other or to analyzers.
- **Storage** is the only place SQL lives. SQLite in WAL mode with compound indexes tuned for the dashboard's query patterns. Provides the core correlation primitive `samples_in_window(start, end)`, returning records from all collectors within a time range.
- **Analyzers** read from storage, apply rule-based thresholds, and emit events with cross-signal evidence. Stateless: all state lives in the database.
- **Dashboard** is a native Windows window (pywebview + WebView2) rendering HTML/CSS/JS with a Chart.js streaming latency oscilloscope. A separate incident window subprocess launches automatically on critical events.

A threaded scheduler runs each collector and analyzer as its own worker with broad exception handling, so one failure cannot crash the system. See `docs/architecture.md` for module layout and data flow.

## Collectors

| Collector | Interval | Signals |
|-----------|----------|---------|
| connectivity | 30s | ICMP RTT and packet loss to 1.1.1.1 and 8.8.8.8 |
| dns | 60s | Resolution time per hostname, with explicit success/failure records |
| route | 300s | tracert hops, hashed as a route fingerprint for path-change detection |
| wifi | 120s | Current RSSI in dBm, link rate, nearby AP inventory (SSID, BSSID, channel) |
| status | 15s | Active TCP probes to gateway, public internet, and VPN-required destinations |
| firewall | 60s | Windows Defender Firewall state per profile (Domain, Private, Public) |

The status collector was originally implemented against passive netsh/ipconfig output but was rewritten to use active TCP probes after real-world testing exposed that Windows keeps VPN adapters listed as "up" long after the tunnel is functionally dead. Active probing tests whether traffic actually reaches destinations, honestly reporting states like `broken` (adapter present but tunnel not delivering).

## Analyzers

| Analyzer | Detects | Severity |
|-----------|---------|----------|
| latency_spike | Current latency at least 3x rolling median AND above 50ms floor | warning |
| dns_outage | 50%+ DNS failures over 40 samples (warning), 90%+ (critical) | warning / critical |
| route_change | Route fingerprint change for a target | info |
| wifi_degradation | RSSI drop of at least 15 dB below baseline AND now below -70 dBm | warning |
| firewall_disabled | Windows Defender Firewall profile transitions enabled to disabled | warning |
| rogue_ap | Known SSID observed from a new BSSID (evil-twin heuristic) | info |

Each threshold is defended with an "honesty check" section in `docs/OPERATIONS.md`. The bar for `critical` severity is deliberately narrow: sustained failure of a whole subsystem, not any single-cycle anomaly. This avoids the alarm-fatigue trap that destroys the value of monitoring tools.

Critical events trigger a full-screen alarm strobe on the main dashboard and open a dedicated incident window with the event summary, a probable-cause hint, and cross-signal evidence from every collector active in the same time window. Past incidents remain browsable via the INCIDENTS button in the event log.

## Testing and engineering discipline

The project has 81 tests across six files covering the storage layer, analyzers, parsers, records, imports, and stress performance. GitHub Actions CI runs the fast suite on every push across Python 3.10, 3.11, and 3.12.

Some of the concrete engineering problems solved during development:

- **Logger file-handle contention on Windows.** Fifteen named loggers each held their own `RotatingFileHandler`, causing rotation to fail with a Windows file lock when logs crossed 1 MB. Fixed by a module-level singleton handler shared across all logger names. `test_smoke.py::test_creating_many_loggers_does_not_crash` guards against regression.
- **Dashboard queries that scanned all rows.** Original dashboard ticks took 3 to 5 seconds because window-function queries against 50K+ samples had no supporting indexes. Adding a compound `(collector, metric, ts DESC)` index and rewriting `ROW_NUMBER() OVER PARTITION` queries as MAX/JOIN patterns dropped tick time from ~3000ms to under 50ms. Stress tests in `tests/test_stress.py` enforce this SLA against 200K seeded samples.
- **VPN state detection was lying.** Passive adapter checks (ipconfig) reported VPN as "connected" long after the tunnel was functionally dead. Rewrote the status collector to use active TCP probes against destinations that require the tunnel, honestly reporting `broken` when the adapter existed but couldn't deliver.
- **Alarm watermark race condition.** After clearing telemetry data (and optionally resetting IDs), new critical events were silently ignored because the alarm watcher's in-memory watermark still referenced pre-clear IDs. Fixed by resetting the watermark to `null` in the clear success handler, letting the watcher's bootstrap logic re-initialize on the next poll.

Engineering practices baked into the codebase:

- **Passive-only design.** No active scanning, no packet injection, no offensive Wi-Fi operations. The rogue AP detector observes what the OS's normal scanning already sees.
- **Parameterized SQL only.** All queries use SQLite parameter substitution. No string concatenation, no injection surface.
- **Subprocess arguments as lists.** All calls to netsh, tracert, ping, ipconfig pass arguments as Python lists to `subprocess.run`, bypassing shell interpretation entirely.
- **Bounded database growth.** Automatic pruning of samples, runs, and events older than 7 days (configurable), running hourly via the scheduler.
- **Bounded UI subprocess spawning.** The test-incident button has both JS-side (5s cooldown) and Python-side (3 launches per 15 seconds) rate limits.

See `docs/OPERATIONS.md` § 6 (Security and engineering posture) for full detail on these decisions.

## What ENACT deliberately does not do

Not every network-adjacent capability belongs in a single-host observability tool. ENACT does not include:

- Machine learning or statistical anomaly detection. Analyzers are rule-based with defended thresholds.
- Active scanning (port scans, service enumeration, Nmap). Nothing that could look like unauthorized probing.
- Packet capture or protocol analysis. No Scapy, no raw sockets, no promiscuous-mode Wi-Fi.
- Distributed or multi-host monitoring. Every observation is from a single machine's perspective.
- Cloud services or hosted deployment. Local install only.
- Authentication or multi-user support. Single operator, single machine.
- Offensive security operations of any kind.

These are called out explicitly because scope was chosen deliberately, not accidentally shallow.

## Documentation

- `docs/OPERATIONS.md`: severity model, per-analyzer specifications with honesty checks, security posture, operator playbook
- `docs/architecture.md`: module layout, data flow, design decisions
- `docs/roadmap.md`: phase history and planned improvements

## Tech stack

Python 3.10+, SQLite (WAL mode), pywebview + WebView2, Chart.js, pytest, GitHub Actions

## License

MIT. See `LICENSE`.