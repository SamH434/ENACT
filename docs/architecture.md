# Basic ENACT Architecture Documentation

## Summary
ENACT is a Python application that continuously gathers network telemetry data on customizable schedules, stores the results in a SQLite database, and displays that information through a dashboard + event log for monitoring and analysis.

## Modules
- `src/collectors/` — gather raw telemetry (ping, DNS, Wi-Fi).
- `src/storage/` — SQLite schema and read/write abstraction.
- `src/analyzers/` — rule based detectors that turn samples into events.
- `src/dashboard/` — visualization layer TODO: flask
- `src/utils/` — shared utilities (logging, config, helpers).

## Flow of data
collectors -> storage.samples -> analyzers -> storage.events -> dashboard

## Diagram
TODO: recreate paper plan to ascii or something

## Section below is gathered through long-term testing

## Known behaviors

**Host sleep/wake:** ENACT runs as a normal user process and gets
suspended when the host sleeps. Long collector cycles (especially
tracert and ping) can appear in the data as a single multi hour
"cycle" after resume. The system handles this gracefully but the
timeline will show a gap. Records produced after wake are stamped
with the wake time, not the original cycle start.