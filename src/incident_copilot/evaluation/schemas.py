"""Validated input and output contracts for deterministic offline evaluation."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from incident_copilot.domain.common import (
    AwareDatetime,
    DomainModel,
    normalize_services,
    unique_evidence_ids,
    unique_non_empty,
)
from incident_copilot.domain.report import IncidentReport


class SampleStatus(StrEnum):
    """Whether a sample produced a report or retained a runner failure."""

    COMPLETED = "completed"
    FAILED = "failed"


class ExpectedToolCall(DomainModel):
    """Expected tool plus only the argument fields relevant to the ground truth."""

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class EvaluationGroundTruth(DomainModel):
    """Labels withheld from the graph and consumed only after inference."""

    affected_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    failure_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    root_cause_terms: tuple[str, ...] = Field(min_length=1, max_length=20)
    relevant_evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    relevant_document_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    expected_tools: tuple[ExpectedToolCall, ...] = Field(default_factory=tuple, max_length=20)

    @field_validator("affected_services")
    @classmethod
    def validate_services(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_services(values)

    @field_validator("root_cause_terms", "relevant_document_ids")
    @classmethod
    def validate_text_collections(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique_non_empty(values, field_name="evaluation labels")

    @field_validator("relevant_evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique_evidence_ids(values, field_name="relevant evidence ids")

    @model_validator(mode="after")
    def validate_unique_tools(self) -> Self:
        names = [item.tool_name for item in self.expected_tools]
        if len(names) != len(set(names)):
            raise ValueError("expected tool names must be unique per sample")
        return self


class EvaluationSample(DomainModel):
    """One reproducible incident invocation and its evaluator-only labels."""

    sample_id: str = Field(pattern=r"^eval_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    fixture_path: str = Field(min_length=1, max_length=512)
    retrieval_query: str = Field(min_length=2, max_length=512)
    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    ground_truth: EvaluationGroundTruth
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=20)

    @field_validator("fixture_path")
    @classmethod
    def validate_relative_fixture_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("fixture_path must be a repository-relative path")
        return path.as_posix()

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique_non_empty(values, field_name="evaluation tags")


class EvaluationDataset(DomainModel):
    """Immutable versioned collection used for comparable offline runs."""

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(pattern=r"^dataset_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_000)
    samples: tuple[EvaluationSample, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_unique_samples(self) -> Self:
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation sample ids must be unique")
        return self


class SetMetrics(DomainModel):
    """Auditable set comparison with explicit counts and empty-set semantics."""

    expected_count: int = Field(ge=0)
    actual_count: int = Field(ge=0)
    true_positive_count: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    exact_match: bool


class ToolArgumentMetrics(DomainModel):
    """Subset argument comparison; unspecified runtime fields are not penalized."""

    expected_field_count: int = Field(ge=0)
    matched_field_count: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)


class RetrievalMetrics(DomainModel):
    """Ranked retrieval labels and hand-checkable Recall@K/MRR outputs."""

    top_k: int = Field(ge=1, le=50)
    expected_document_ids: tuple[str, ...]
    ranked_document_ids: tuple[str, ...]
    recall_at_k: float = Field(ge=0.0, le=1.0)
    reciprocal_rank: float = Field(ge=0.0, le=1.0)


class CitationMetrics(DomainModel):
    """Exact citation integrity for every EvidenceRef attached to the report."""

    checked_evidence_count: int = Field(ge=0)
    correct_citation_count: int = Field(ge=0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)


class ActualToolCall(DomainModel):
    """Raw completed tool record reconstructed from the executed plan."""

    tool_name: str
    arguments: dict[str, JsonValue]
    status: str
    evidence_ids: tuple[str, ...]


class SampleUsage(DomainModel):
    """Measured graph counters plus explicit token provenance and unavailable cost."""

    research_rounds: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    token_usage_estimated: bool
    estimated_cost_usd: None = None
    cost_status: Literal["unavailable_no_pricing"] = "unavailable_no_pricing"


class EvaluationSampleResult(DomainModel):
    """Raw, traceable result retained even when one sample fails."""

    sample_id: str
    status: SampleStatus
    error: str | None = Field(default=None, max_length=2_000)
    predicted_services: tuple[str, ...] = ()
    predicted_failure_type: str | None = None
    root_cause: str | None = None
    service_localization: SetMetrics | None = None
    failure_type_correct: bool | None = None
    retrieval: RetrievalMetrics | None = None
    tool_selection: SetMetrics | None = None
    tool_arguments: ToolArgumentMetrics | None = None
    evidence_relevance: SetMetrics | None = None
    citations: CitationMetrics | None = None
    root_cause_term_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    root_cause_accurate: bool | None = None
    actual_tool_calls: tuple[ActualToolCall, ...] = ()
    usage: SampleUsage | None = None
    report: IncidentReport | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is SampleStatus.COMPLETED and (
            self.report is None or self.error is not None
        ):
            raise ValueError("completed evaluation sample requires a report and no error")
        if self.status is SampleStatus.FAILED and not self.error:
            raise ValueError("failed evaluation sample requires an error")
        return self


class AggregateMetrics(DomainModel):
    """Means over completed samples; optional metrics exclude undefined denominators."""

    service_localization_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    failure_type_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    tool_selection_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    tool_argument_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_relevance_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_correctness: float | None = Field(default=None, ge=0.0, le=1.0)
    root_cause_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_research_rounds: float | None = Field(default=None, ge=0.0)
    mean_tool_calls: float | None = Field(default=None, ge=0.0)
    mean_latency_ms: float | None = Field(default=None, ge=0.0)
    p95_latency_ms: float | None = Field(default=None, ge=0.0)
    total_tokens: int = Field(ge=0)
    mean_tokens: float | None = Field(default=None, ge=0.0)
    token_usage_estimated: bool | None = None
    estimated_cost_usd: None = None
    cost_status: Literal["unavailable_no_pricing"] = "unavailable_no_pricing"


class EvaluationSummary(DomainModel):
    """Aggregate report tied to one dataset version and raw-result artifact."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=r"^evalrun_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    dataset_id: str
    dataset_version: str
    started_at: AwareDatetime
    completed_at: AwareDatetime
    sample_count: int = Field(ge=0)
    completed_sample_count: int = Field(ge=0)
    failed_sample_count: int = Field(ge=0)
    metrics: AggregateMetrics
    raw_results_file: str
    limitations: tuple[str, ...]
    generated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.completed_sample_count + self.failed_sample_count != self.sample_count:
            raise ValueError("evaluation sample counts must balance")
        if self.completed_at < self.started_at:
            raise ValueError("evaluation completion must not precede start")
        return self
