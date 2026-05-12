<p align="left">
  <img src="docs/assets/ENACTLogoWeb.png" alt="ENACT logo" width="300">

</p>

# ENACT (Engine for Network Anomaly, Condition, and Telemetry)

ENACT is a lightweight Python network observability and telemetry platform. ENACT monitors
connectivity health, DNS reliability, route stability, and Wi-Fi conditions, then
stores telemetry, surfaces trends, and logs abnormal events. This project is basic
in scale but more importantly serves as an scalable "base chassis" for future projects in parallel fields.

## Features

- **Connectivity telemetry** : ping latency, packet loss, and jitter estimate
  against configurable public targets.
- **DNS monitoring** : resolution time per hostname, with explicit success and
  failure records.
- **Route monitoring** : periodic `tracert` with hop fingerprinting for path
  stability and change detection.
- **Wi-Fi telemetry** : current link state plus nearby access-point inventory
  (signal, channel, BSSID) for RF environment awareness.
- **Uniform telemetry contract** : every collector emits `TelemetryRecord`
  objects with the same shape, regardless of data source.

## Installation
Requires Python 3.10+ on Windows

```powershell
git clone https://github.com/SamH434/ENACT.git
cd ENACT
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Documentation
- `docs/architecture.md` — module layout and data flow
- `docs/roadmap.md` — phase plan, includes updated planning process