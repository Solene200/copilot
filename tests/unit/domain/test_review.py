"""Validation tests for human review commands."""

import pytest
from pydantic import ValidationError

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.hypothesis import VerificationQuery
from incident_copilot.domain.review import HumanFeedback, ReviewAction


def test_accept_rejects_additional_queries() -> None:
    with pytest.raises(ValidationError):
        HumanFeedback(
            action=ReviewAction.ACCEPT,
            requested_queries=(
                VerificationQuery(
                    query="unexpected query",
                    source_types=(SourceType.LOG,),
                ),
            ),
        )


def test_additional_research_requires_a_query() -> None:
    with pytest.raises(ValidationError):
        HumanFeedback(action=ReviewAction.REQUEST_MORE_RESEARCH)
