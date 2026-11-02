"""Bounded sequence generation is a public, deterministic discovery interface."""

import pytest


def test_sequences_include_reverts_and_stop_at_the_requested_cap():
    from sieve_tool.testing.sequences import bounded_sequences

    assert bounded_sequences(
        initial="a", states=("a", "b", "c"), max_steps=2, max_sequences=4
    ) == [("b",), ("c",), ("b", "a"), ("b", "c")]


@pytest.mark.parametrize(
    ("steps", "cases"),
    [(0, 1), (4, 1), (1, 0), (1, 17), (True, 1), (1, False), (1.0, 1), (1, 1.0)],
)
def test_sequence_bounds_must_be_small_positive_integers(steps, cases):
    from sieve_tool.testing.sequences import bounded_sequences

    with pytest.raises(ValueError, match="bounds"):
        bounded_sequences(
            initial="a", states=("a", "b"), max_steps=steps, max_sequences=cases
        )


@pytest.mark.parametrize(
    "states", [(), ("a",), ("a", "a"), ("b", "c"), ("a", ""), tuple("abcde")]
)
def test_catalog_requires_two_to_four_distinct_states_including_the_initial(states):
    from sieve_tool.testing.sequences import bounded_sequences

    with pytest.raises(ValueError, match="states"):
        bounded_sequences(initial="a", states=states, max_steps=2, max_sequences=4)
