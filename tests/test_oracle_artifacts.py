"""Malformed saved evidence is identified before comparison."""

import json

import pytest

from sieve_tool.oracle.artifacts import (
    ArtifactError,
    read_digest,
    read_family,
    read_history,
    read_mask,
    read_outcome,
    read_state,
)


@pytest.mark.parametrize(
    "reader,value,field",
    [
        (read_state, {"secret/default/example": 42}, "secret/default/example"),
        (read_state, [], "<root>"),
        (read_digest, {"secret/default/example": {"ADDED": True}}, "ADDED"),
        (read_family, [1], "0"),
        (read_mask, {"secret/default/example": [[1]]}, "0"),
        (read_history, [{"number": 1, "etype": "ADDED", "key": "one"}], "state"),
        (read_outcome, {"status": "completed", "returncode": "0"}, "returncode"),
    ],
)
def test_shape_errors_include_path_and_field_without_input_values(
    tmp_path, reader, value, field
):
    source = tmp_path / "artifact.json"
    source.write_text(json.dumps(value))
    before = source.read_bytes()
    with pytest.raises(ArtifactError) as error:
        reader(source)
    assert str(source) in str(error.value)
    assert field in str(error.value)
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "text", ['{"key":{},"key":{}}', "{broken", '{"key":{"value":NaN}}']
)
def test_invalid_json_is_not_silently_accepted(tmp_path, text):
    source = tmp_path / "state.json"
    source.write_text(text)
    with pytest.raises(ArtifactError, match="state.json"):
        read_state(source)


def test_missing_evidence_is_an_artifact_error(tmp_path):
    with pytest.raises(ArtifactError, match="missing.json"):
        read_state(tmp_path / "missing.json")


def test_legacy_masks_and_opaque_kubernetes_fields_are_retained(tmp_path):
    value = {
        "secret/default/random": "SIEVE-IGNORE",
        "custom/default/one": {"newSchema": [1, None, {"flag": True}]},
    }
    source = tmp_path / "state.json"
    source.write_text(json.dumps(value))
    assert read_state(source) == value


def test_outcome_metadata_and_absent_fields_keep_existing_validation_semantics(
    tmp_path,
):
    source = tmp_path / "process-outcome.json"
    source.write_text('{"status":"future-status","diagnostic":{"anything":true}}')
    outcome = read_outcome(source)
    assert outcome.status == "future-status" and outcome.returncode is None
