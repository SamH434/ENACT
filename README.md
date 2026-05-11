<p align="left">
  <img src="docs/assets/ENACTLogoWeb.png" alt="ENACT logo" width="300">
</p>

# ENACT (Engine for Network Anomaly, Condition, and Telemetry)

ENACT is lightweight Python network observability and telemetry platform. ENACT monitors
connectivity health, DNS reliability, route stability, and Wi-Fi conditions, then
stores telemetry, surfaces trends, and logs abnormal events. This project is basic
in scale but more importantly serves as an scalable "base chassis" for future projects in parallel fields.

## Installation
Requires Python 3.10+ on Windows

```powershell
git clone https://github.com/<SamH434>/ENACT.git
cd ENACT
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Documentation
- `docs/architecture.md` — module layout and data flow
- `docs/roadmap.md` — phase plan, includes updated planning process