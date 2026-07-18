"""Versioned HTTP schemas for investigation lifecycle operations."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from incident_copilot.api.schemas import ApiModel
from incident_copilot.core.logging import redact_value
from incident_copilot.domain.common import (
    AwareDatetime,
    Environment,
    Severity,
    normalize_services,
    unique_non_empty,
)
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.report import IncidentReport
from incident_copilot.domain.review import HumanFeedback, HumanReviewRequest
from incident_copilot.graph.schemas import InvestigationOptions
from incident_copilot.investigations.models import InvestigationRecord, InvestigationStatus


class CreateInvestigationRequest(ApiModel):
    """Validated user scope and bounded execution policy."""

    query: str = Field(min_length=1, max_length=10_000)
    services: tuple[str, ...] = Field(min_length=1, max_length=20)
    start_time: AwareDatetime
    end_time: AwareDatetime
    symptoms: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    severity: Severity = Severity.UNKNOWN
    environment: Environment = Environment.UNKNOWN
    options: InvestigationOptions = Field(default_factory=InvestigationOptions)

    @field_validator("services")
    @classmethod
    def validate_services(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_services(values)

    @field_validator("symptoms")
    @classmethod
    def validate_symptoms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique_non_empty(values, field_name="symptoms")

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self

    def fingerprint(self) -> str:
        """Hash the semantic request before server-generated IDs are introduced."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_incident(self, incident_id: str) -> IncidentContext:
        """Translate the transport request into the domain boundary."""
        return IncidentContext(
            incident_id=incident_id,
            raw_query=self.query,
            services=self.services,
            start_time=self.start_time,
            end_time=self.end_time,
            symptoms=self.symptoms,
            severity=self.severity,
            environment=self.environment,
            created_at=datetime.now(UTC),
        )


class ResumeInvestigationRequest(HumanFeedback):
    """Public resume body reusing the strict domain feedback contract."""


class InvestigationResponse(ApiModel):
    """Public task projection without raw graph checkpoint values."""

    schema_version: str = "1.0"
    investigation_id: str
    incident_id: str
    thread_id: str
    run_id: str
    status: InvestigationStatus
    review_required: bool
    review_request: HumanReviewRequest | None
    report: IncidentReport | None
    error_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    replayed: bool = False

    @classmethod
    def from_record(
        cls,
        record: InvestigationRecord,
        *,
        replayed: bool = False,
    ) -> "InvestigationResponse":
        """Build one stable response from repository metadata."""
        report = (
            IncidentReport.model_validate(redact_value(record.report.model_dump(mode="python")))
            if record.report is not None
            else None
        )
        review_request = (
            HumanReviewRequest.model_validate(
                redact_value(record.review_request.model_dump(mode="python"))
            )
            if record.review_request is not None
            else None
        )
        return cls(
            investigation_id=record.investigation_id,
            incident_id=record.incident_id,
            thread_id=record.thread_id,
            run_id=record.run_id,
            status=record.status,
            review_required=record.status is InvestigationStatus.WAITING_REVIEW,
            review_request=review_request,
            report=report,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            replayed=replayed,
        )
