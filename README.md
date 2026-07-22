<p align="left">
  <img src="docs/assets/ENACTLogoWeb.png" alt="ENACT logo" width="300">
</p>

<p align="left">
  <img src="https://github.com/SamH434/ENACT/actions/workflows/test.yml/badge.svg" alt="tests status">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey" alt="Windows 10/11">
</p>

# ENACT (Engine for Network Anomaly, Condition, and Telemetry)

ENACT is a lightweight Python-based network observability and telemetry platform that uses correlation-aware observability for anomaly detection. Each telemetry cycle is tagged with a shared correlation_id, allowing signals from all collectors to be analyzed as a unified observation window rather than isolated timelines. This enables the analyzer and dashboard layers to surface ranked, cross-signal anomalies with supporting evidence.

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
- `docs/architecture.md`: module layout and data flow
- `docs/roadmap.md`: phase plan, includes updated planning process