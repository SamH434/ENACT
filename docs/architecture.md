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