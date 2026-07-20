"""
Tests for the TelemetryRecord contract and run_id generation
"""


def test_new_run_id_is_unique():
    """Consecutive calls produce different IDs."""
    from src.utils.records import new_run_id

    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100, "run_id should be unique enough for reasonable use"


def test_new_run_id_is_short_hex():
    """run_id is a short hex string, easy to log."""
    from src.utils.records import new_run_id

    rid = new_run_id()
    assert isinstance(rid, str)
    assert len(rid) == 12
    assert all(c in "0123456789abcdef" for c in rid)


def test_telemetry_record_defaults():
    """Optional fields default sensibly."""
    from src.utils.records import TelemetryRecord

    r = TelemetryRecord(collector="c", metric="m", value=1.0)
    assert r.run_id is None
    assert r.metadata == {}
    assert r.timestamp is not None


def test_telemetry_record_to_dict_serializes_timestamp():
    """to_dict converts the datetime to an ISO string, ready for JSON."""
    from src.utils.records import TelemetryRecord

    r = TelemetryRecord(collector="c", metric="m", value=1.0)
    d = r.to_dict()
    assert isinstance(d["timestamp"], str)
    assert "T" in d["timestamp"]  # ISO format includes 'T'


def test_telemetry_record_accepts_string_value():
    """String values are supported for metrics like route fingerprint."""
    from src.utils.records import TelemetryRecord

    r = TelemetryRecord(collector="route", metric="route_fingerprint",
                        value="abc123def456")
    assert r.value == "abc123def456"


def test_telemetry_record_accepts_none_value():
    """None value is supported for failed measurements (e.g. DNS lookup fail)."""
    from src.utils.records import TelemetryRecord

    r = TelemetryRecord(collector="dns", metric="resolution_ms", value=None,
                        metadata={"success": False})
    assert r.value is None