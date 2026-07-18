"""Reducer properties required for parallel LangGraph state updates."""

from incident_copilot.domain.evidence import EvidenceRef
from incident_copilot.graph.state import add_count, merge_evidence
from incident_copilot.tools.providers.fixture import FixtureProvider


def refs() -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef.from_evidence(item)
        for item in FixtureProvider.payment_service().fixture.evidence[:3]
    )


def test_evidence_reducer_is_idempotent_and_order_independent() -> None:
    first, second, third = refs()

    assert merge_evidence((first, second), (second, third)) == merge_evidence(
        (third, second), (first,)
    )
    assert merge_evidence((first,), (first,)) == (first,)


def test_evidence_reducer_is_associative() -> None:
    first, second, third = refs()

    left_grouped = merge_evidence(merge_evidence((first,), (second,)), (third,))
    right_grouped = merge_evidence((first,), merge_evidence((second,), (third,)))

    assert left_grouped == right_grouped


def test_parallel_counter_reducer_adds_branch_deltas() -> None:
    assert add_count(add_count(0, 1), 1) == 2
    assert add_count(3, 4) == add_count(4, 3)
