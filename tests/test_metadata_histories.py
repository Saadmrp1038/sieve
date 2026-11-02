"""Metadata histories exercise map entry lifetime through the public catalog."""

import pytest


def test_bounded_metadata_catalog_includes_change_and_cleanup_in_both_maps():
    from sieve_tool.testing.sequences import metadata_histories

    histories = metadata_histories()
    assert len(histories) == 8
    for channel, index in (("labels", 3), ("annotations", 7)):
        history = histories[index]
        assert history.channel == channel
        assert [(step.operation, step.value) for step in history.steps] == [
            ("add", "one"),
            ("change", "two"),
            ("remove", None),
        ]
    assert [len(h.steps) for h in metadata_histories(max_histories=2)] == [1, 2]


@pytest.mark.parametrize("limit", [0, 9, True, 1.5])
def test_metadata_catalog_rejects_unbounded_or_ambiguous_limits(limit):
    from sieve_tool.testing.sequences import metadata_histories

    with pytest.raises(ValueError, match="1–8"):
        metadata_histories(max_histories=limit)
