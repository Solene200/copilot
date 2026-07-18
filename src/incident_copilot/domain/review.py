"""Human review values shared by the graph and application layer."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from incident_copilot.domain.common import DomainModel
from incident_copilot.domain.hypothesis import VerificationQuery


class ReviewAction(StrEnum):
    """Allow-listed decisions accepted by a paused investigation."""

    ACCEPT = "accept"
    REQUEST_MORE_RESEARCH = "request_more_research"


class HumanFeedback(DomainModel):
    """Validated resume payload; arbitrary graph commands are never accepted."""

    action: ReviewAction
    comment: str | None = Field(default=None, max_length=2_000)
    requested_queries: tuple[VerificationQuery, ...] = Field(
        default_factory=tuple,
        max_length=10,
    )

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if self.action is ReviewAction.ACCEPT and self.requested_queries:
            raise ValueError("accept feedback must not include requested queries")
        if self.action is ReviewAction.REQUEST_MORE_RESEARCH and not self.requested_queries:
            raise ValueError("additional research requires at least one query")
        return self


class HumanReviewRequest(DomainModel):
    """Small JSON-safe interrupt payload without raw graph state."""

    schema_version: str = "1.0"
    report_id: str = Field(pattern=r"^rpt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    reason: str = Field(min_length=1, max_length=500)
    high_risk_actions: tuple[str, ...] = Field(min_length=1, max_length=20)
    allowed_actions: tuple[ReviewAction, ...] = (
        ReviewAction.ACCEPT,
        ReviewAction.REQUEST_MORE_RESEARCH,
    )
