"""Offline evaluation orchestration and auditable artifact generation."""

import json
import uuid
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from incident_copilot.evaluation.dataset import resolve_fixture_path
from incident_copilot.evaluation.evaluators import (
    aggregate_metrics,
    citation_metrics,
    classify_failure_type,
    json_argument_value,
    retrieval_metrics,
    root_cause_term_recall,
    set_metrics,
    tool_argument_metrics,
)
from incident_copilot.evaluation.schemas import (
    ActualToolCall,
    EvaluationDataset,
    EvaluationSample,
    EvaluationSampleResult,
    EvaluationSummary,
    SampleStatus,
    SampleUsage,
)
from incident_copilot.graph import build_offline_investigation_graph, create_initial_state
from incident_copilot.graph.state import InvestigationState
from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.schemas import MetadataFilter, SearchQuery
from incident_copilot.tools.providers.fixture import FixtureProvider

ROOT_CAUSE_ACCURACY_THRESHOLD = 0.75


class OfflineEvaluationRunner:
    """Run fixture/Fake-Model evaluation without exposing labels to the graph."""

    def __init__(self, *, enable_langsmith: bool = False, project_name: str | None = None) -> None:
        self._enable_langsmith = enable_langsmith
        self._project_name = project_name or "incident-copilot-offline-evaluation"

    async def run(self, dataset: EvaluationDataset, output_dir: Path) -> EvaluationSummary:
        """Evaluate every sample, retain failures, and write raw plus aggregate artifacts."""
        started_at = datetime.now(UTC)
        run_id = f"evalrun_{started_at:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_path = output_dir / "raw-results.jsonl"
        results: list[EvaluationSampleResult] = []
        retriever, _ = build_fixture_retriever()

        with self._tracing_context():
            for sample in dataset.samples:
                try:
                    result = await self._run_sample(sample, retriever=retriever, run_id=run_id)
                except Exception as exc:  # retained as data so aggregate remains traceable
                    result = EvaluationSampleResult(
                        sample_id=sample.sample_id,
                        status=SampleStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                results.append(result)

        raw_path.write_text(
            "".join(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                + "\n"
                for result in results
            ),
            encoding="utf-8",
        )
        completed_at = datetime.now(UTC)
        completed_count = sum(result.status is SampleStatus.COMPLETED for result in results)
        summary = EvaluationSummary(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            started_at=started_at,
            completed_at=completed_at,
            sample_count=len(results),
            completed_sample_count=completed_count,
            failed_sample_count=len(results) - completed_count,
            metrics=aggregate_metrics(results),
            raw_results_file=raw_path.name,
            limitations=(
                "This is a fixture regression evaluation, not a production generalization claim.",
                "Latency is single-process wall-clock time on the current machine, "
                "not a benchmark.",
                "Fake Model token counts are deterministic character-based estimates.",
                "Root-cause accuracy uses versioned lexical indicators, not an LLM-as-judge.",
                "Cost is unavailable because no provider pricing was configured.",
            ),
        )
        (output_dir / "summary.json").write_text(
            summary.model_dump_json(indent=2), encoding="utf-8"
        )
        (output_dir / "summary.md").write_text(self._render_markdown(summary), encoding="utf-8")
        return summary

    async def _run_sample(
        self,
        sample: EvaluationSample,
        *,
        retriever: Any,
        run_id: str,
    ) -> EvaluationSampleResult:
        fixture_provider = FixtureProvider.from_path(resolve_fixture_path(sample.fixture_path))
        incident = fixture_provider.fixture.incident

        retrieval = retriever.search(
            SearchQuery(
                query=sample.retrieval_query,
                top_k=sample.retrieval_top_k,
                metadata_filter=MetadataFilter(services=incident.services),
            )
        )
        ranked_document_ids = tuple(hit.chunk.document_id for hit in retrieval.hits)
        retrieval_score = retrieval_metrics(
            sample.ground_truth.relevant_document_ids,
            ranked_document_ids,
            top_k=sample.retrieval_top_k,
        )

        graph = build_offline_investigation_graph(fixture_provider=fixture_provider)
        started = perf_counter()
        state = cast(
            InvestigationState,
            await graph.ainvoke(
                create_initial_state(incident),
                config={
                    "run_name": f"offline-evaluation:{sample.sample_id}",
                    "tags": ["offline-evaluation", dataset_tag(run_id)],
                    "metadata": {
                        "evaluation_run_id": run_id,
                        "sample_id": sample.sample_id,
                        "dataset_ground_truth_exposed": False,
                    },
                },
            ),
        )
        latency_ms = (perf_counter() - started) * 1_000
        report = state["final_report"]
        actual_calls = self._actual_tool_calls(state)
        predicted_failure_type = classify_failure_type(report.root_cause)
        root_recall = root_cause_term_recall(
            report.root_cause, sample.ground_truth.root_cause_terms
        )
        actual_evidence_ids = tuple(item.evidence_id for item in report.supporting_evidence)
        stats = report.investigation_stats
        return EvaluationSampleResult(
            sample_id=sample.sample_id,
            status=SampleStatus.COMPLETED,
            predicted_services=report.affected_services,
            predicted_failure_type=predicted_failure_type,
            root_cause=report.root_cause,
            service_localization=set_metrics(
                sample.ground_truth.affected_services, report.affected_services
            ),
            failure_type_correct=predicted_failure_type == sample.ground_truth.failure_type,
            retrieval=retrieval_score,
            tool_selection=set_metrics(
                (item.tool_name for item in sample.ground_truth.expected_tools),
                (item.tool_name for item in actual_calls),
            ),
            tool_arguments=tool_argument_metrics(sample.ground_truth.expected_tools, actual_calls),
            evidence_relevance=set_metrics(
                sample.ground_truth.relevant_evidence_ids, actual_evidence_ids
            ),
            citations=citation_metrics(report),
            root_cause_term_recall=root_recall,
            root_cause_accurate=root_recall >= ROOT_CAUSE_ACCURACY_THRESHOLD,
            actual_tool_calls=actual_calls,
            usage=SampleUsage(
                research_rounds=stats.research_rounds,
                tool_calls=stats.tool_call_count,
                model_calls=stats.model_call_count,
                latency_ms=latency_ms,
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                total_tokens=stats.total_tokens,
                token_usage_estimated=stats.token_usage_estimated,
            ),
            report=report,
        )

    @staticmethod
    def _actual_tool_calls(state: InvestigationState) -> tuple[ActualToolCall, ...]:
        calls: list[ActualToolCall] = []
        for result in state.get("completed_steps", ()):
            arguments = {key: json_argument_value(value) for key, value in result.arguments.items()}
            calls.append(
                ActualToolCall(
                    tool_name=result.tool_name,
                    arguments=arguments,
                    status=result.status.value,
                    evidence_ids=result.evidence_ids,
                )
            )
        return tuple(sorted(calls, key=lambda item: (item.tool_name, str(item.arguments))))

    def _tracing_context(self) -> AbstractContextManager[object]:
        try:
            from langsmith import tracing_context
        except ImportError:
            if self._enable_langsmith:
                raise RuntimeError(
                    "LangSmith tracing was requested but the SDK is unavailable"
                ) from None
            return nullcontext()

        return tracing_context(
            enabled=self._enable_langsmith,
            project_name=self._project_name if self._enable_langsmith else None,
        )

    @staticmethod
    def _render_markdown(summary: EvaluationSummary) -> str:
        metrics = summary.metrics

        def render(value: float | None) -> str:
            return "N/A" if value is None else f"{value:.4f}"

        return "\n".join(
            (
                "# IncidentCopilot Offline Evaluation",
                "",
                f"- Run: `{summary.run_id}`",
                f"- Dataset: `{summary.dataset_id}` version `{summary.dataset_version}`",
                f"- Samples: {summary.sample_count} "
                f"({summary.completed_sample_count} completed, "
                f"{summary.failed_sample_count} failed)",
                f"- Raw results: `{summary.raw_results_file}`",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                "| Service localization accuracy | "
                f"{render(metrics.service_localization_accuracy)} |",
                f"| Failure type accuracy | {render(metrics.failure_type_accuracy)} |",
                f"| Retrieval Recall@K | {render(metrics.retrieval_recall_at_k)} |",
                f"| Retrieval MRR | {render(metrics.retrieval_mrr)} |",
                f"| Tool selection F1 | {render(metrics.tool_selection_f1)} |",
                f"| Tool argument accuracy | {render(metrics.tool_argument_accuracy)} |",
                f"| Evidence relevance F1 | {render(metrics.evidence_relevance_f1)} |",
                f"| Citation correctness | {render(metrics.citation_correctness)} |",
                f"| Root-cause accuracy | {render(metrics.root_cause_accuracy)} |",
                f"| Mean research rounds | {render(metrics.mean_research_rounds)} |",
                f"| Mean tool calls | {render(metrics.mean_tool_calls)} |",
                f"| Mean latency (ms) | {render(metrics.mean_latency_ms)} |",
                f"| P95 latency (ms) | {render(metrics.p95_latency_ms)} |",
                f"| Total tokens | {metrics.total_tokens} |",
                f"| Mean tokens | {render(metrics.mean_tokens)} |",
                f"| Token usage estimated | {metrics.token_usage_estimated} |",
                "| Estimated cost | N/A (no pricing configured) |",
                "",
                "## Limitations",
                "",
                *(f"- {item}" for item in summary.limitations),
                "",
            )
        )


def dataset_tag(run_id: str) -> str:
    """Create a stable, allow-listed trace tag from the generated run id."""
    return f"evaluation-run:{run_id}"
