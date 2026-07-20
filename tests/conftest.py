"""
Shared pytest fixtures for the ENACT test suite.
"""

import sys
from pathlib import Path

import pytest

# regardless of where pytest is invoked from
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Redirect the database module to a fresh SQLite file for this test."""
    from src.storage import database

    db_path = tmp_path / "test_enact.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield db_path

# most tests need to inject data, this saves boilerplate on every test
@pytest.fixture
def make_record():
    from src.utils.records import TelemetryRecord, new_run_id
    from datetime import datetime, timezone

    def _make(collector="connectivity", metric="latency_ms",
              value=25.0, metadata=None, timestamp=None, run_id=None):
        r = TelemetryRecord(
            collector=collector,
            metric=metric,
            value=value,
            run_id=run_id or new_run_id(),
            metadata=metadata or {},
        )
        if timestamp is not None:
            r.timestamp = timestamp
        return r
    return _make