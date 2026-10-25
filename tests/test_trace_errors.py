"""Malformed records and unsupported histories fail as harness input errors."""

import json
from pathlib import Path

import pytest

from sieve_tool.analysis.trace import assemble_trace
from sieve_tool.trace import (
    Marker,
    TraceError,
    decode_line,
    iter_api_events,
    read_trace,
)

OBJECT = json.dumps({"metadata": {"name": "one", "namespace": "default"}})


def record(marker, *fields):
    return "fixture.go:1: " + "\t".join([marker, *fields]) + "\n"


def project(path):
    return assemble_trace(
        read_trace(path), compress_trivial=True, loosen_boundary=False
    )


@pytest.mark.parametrize(
    "line",
    [
        record(Marker.BEFORE_HEAR, "42", "Added", "secret", "{broken"),
        record(Marker.BEFORE_HEAR, "42", "Added", "secret", "{}"),
        record(Marker.BEFORE_HEAR, "42", "Added", "secret", "[]"),
        record(Marker.AFTER_HEAR),
        record(Marker.AFTER_HEAR, "not-an-id"),
        record(
            Marker.CACHE_READ,
            "Get",
            "secret",
            "default",
            "one",
            "reconciler",
            "NoError",
            "[]",
        ),
    ],
)
def test_bad_records_include_source_line_and_marker(tmp_path, line):
    path = tmp_path / "source.log"
    path.write_text("ordinary log\n" + line)
    original = path.read_bytes()
    with pytest.raises(TraceError) as error:
        read_trace(path)
    assert f"{path}:2:" in str(error.value)
    assert "[SIEVE-" in str(error.value)
    assert path.read_bytes() == original


def test_partial_operations_remain_open_and_projections_are_independent(tmp_path):
    path = tmp_path / "interrupted.log"
    path.write_text(
        record(Marker.BEFORE_HEAR, "90", "Added", "secret", OBJECT)
        + record(Marker.BEFORE_WRITE, "2")
        + record(Marker.BEFORE_RECONCILE, "reconciler", "001")
        + "unrelated trailing log\n"
    )
    parsed = read_trace(path)
    first = assemble_trace(parsed, compress_trivial=True, loosen_boundary=False)
    assert first.hears[0].start_timestamp == 0
    assert first.hears[0].end_timestamp == 4
    assert len(first.reconciles) == 1
    assert first.reconciles[0].reconcile_id == "001"
    first.hears[0].cancelled_by.add(10)
    second = assemble_trace(parsed, compress_trivial=True, loosen_boundary=False)
    assert second.hears[0].cancelled_by == set()
    assert parsed.records[0].event.start_timestamp == -1


@pytest.mark.parametrize(
    "lines",
    [
        record(Marker.AFTER_HEAR, "9"),
        record(
            Marker.AFTER_WRITE,
            "9",
            "Create",
            "reconciler",
            "NoError",
            "secret",
            "default",
            "one",
            OBJECT,
        ),
        record(Marker.BEFORE_HEAR, "9", "Added", "secret", OBJECT) * 2,
        record(Marker.BEFORE_WRITE, "9") * 2,
        record(Marker.BEFORE_RECONCILE, "same", "1")
        + record(Marker.BEFORE_RECONCILE, "same", "2"),
        record(Marker.AFTER_RECONCILE, "missing", "1"),
    ],
)
def test_unsupported_pairings_are_explicit_errors(tmp_path, lines):
    path = tmp_path / "unsupported.log"
    path.write_text(lines)
    with pytest.raises(TraceError):
        project(path)


def test_api_reader_preserves_its_marker_scope(tmp_path):
    path = tmp_path / "api.log"
    path.write_text(
        record(Marker.BEFORE_HEAR, "incomplete")
        + record(
            Marker.API_EVENT, "ADDED", "original", "secret", "default", "one", OBJECT
        )
    )
    events = list(iter_api_events(path))
    assert len(events) == 1 and events[0].key == "secret/default/one"
    with pytest.raises(TraceError):
        read_trace(path)


def test_duplicate_list_entries_are_not_silently_deduplicated(tmp_path):
    value = json.loads(OBJECT)
    line = record(
        Marker.CACHE_READ,
        "List",
        "secret",
        "reconciler",
        "NoError",
        json.dumps([value, value]),
    )
    with pytest.raises(TraceError, match="duplicate List entry"):
        decode_line(line, tmp_path / "source.log", 0)


def test_cache_source_is_recorded_without_changing_the_legacy_decoded_flag():
    line = record(
        Marker.CACHE_READ,
        "Get",
        "secret",
        "default",
        "one",
        "reconciler",
        "NoError",
        OBJECT,
    )
    decoded = decode_line(line, Path("source.log"), 0)
    assert decoded.marker == Marker.CACHE_READ
    assert decoded.event.from_cache is False


def test_marker_text_inside_a_json_payload_is_not_a_second_record():
    obj = {"metadata": {"name": "one"}, "spec": {"note": Marker.BEFORE_HEAR}}
    line = record(
        Marker.API_EVENT,
        "ADDED",
        "original",
        "example",
        "default",
        "one",
        json.dumps(obj),
    )
    decoded = decode_line(line, "source.log", 0)
    assert decoded.marker == Marker.API_EVENT
    assert decoded.event.obj_map == obj
