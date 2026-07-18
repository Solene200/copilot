"""End-to-end Phase 6 evaluation over Fixture, RAG, Fake Model, and LangGraph."""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from incident_copilot.evaluation import OfflineEvaluationRunner, load_evaluation_dataset
from incident_copilot.evaluation.schemas import EvaluationDataset
from incident_copilot.graph.schemas import StepResult, StepStatus
from incident_copilot.graph.state import InvestigationState
from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.schemas import RetrievalResult, SearchQuery


@pytest.mark.asyncio
async def test_offline_evaluation_writes_raw_and_summary_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_network(self: socket.socket, address: object) -> None:
        del self, address
        raise AssertionError("offline evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    dataset = load_evaluation_dataset()

    summary = await OfflineEvaluationRunner().run(dataset, tmp_path)

    assert summary.sample_count == 3
    assert summary.completed_sample_count == 3
    assert summary.failed_sample_count == 0
    assert summary.metrics.token_usage_estimated is True
    assert summary.metrics.estimated_cost_usd is None
    raw_lines = (tmp_path / "raw-results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3
    raw = [json.loads(line) for line in raw_lines]
    assert {item["sample_id"] for item in raw} == {
        "eval_payment_pool_exhaustion",
        "eval_checkout_dns_misconfiguration",
        "eval_inventory_cache_regression",
    }
    assert all(item["report"] is not None for item in raw)
    assert all(item["usage"]["token_usage_estimated"] is True for item in raw)
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "summary.md").is_file()


@pytest.mark.asyncio
async def test_failed_sample_is_retained_in_raw_results_and_denominator(tmp_path: Path) -> None:
    original = load_evaluation_dataset()
    missing = original.samples[0].model_copy(
        update={"fixture_path": "data/incidents/does-not-exist.json"}
    )
    dataset = EvaluationDataset(
        dataset_id="dataset_failure_retention",
        version="1.0.0",
        description="One deliberately unavailable fixture for runner failure retention.",
        samples=(missing,),
    )

    summary = await OfflineEvaluationRunner().run(dataset, tmp_path)

    assert summary.sample_count == 1
    assert summary.completed_sample_count == 0
    assert summary.failed_sample_count == 1
    raw = json.loads((tmp_path / "raw-results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert raw["status"] == "failed"
    assert raw["error"].startswith("FileNotFoundError:")
    assert summary.metrics.root_cause_accuracy is None
    assert summary.metrics.total_tokens == 0


@pytest.mark.asyncio
async def test_retrieval_filter_comes_from_incident_not_ground_truth() -> None:
    dataset = load_evaluation_dataset()
    original = dataset.samples[0]
    poisoned = original.model_copy(
        update={
            "ground_truth": original.ground_truth.model_copy(
                update={"affected_services": ("checkout-service",)}
            )
        }
    )
    retriever, _ = build_fixture_retriever()

    class RecordingRetriever:
        def __init__(self) -> None:
            self.query: SearchQuery | None = None

        def search(self, query: SearchQuery) -> RetrievalResult:
            self.query = query
            return retriever.search(query)

    recording = RecordingRetriever()

    await OfflineEvaluationRunner()._run_sample(
        poisoned,
        retriever=recording,
        run_id="evalrun_test_ground_truth_isolation",
    )

    assert recording.query is not None
    assert recording.query.metadata_filter.services == ("payment-service",)


def test_runner_keeps_arguments_from_steps_outside_the_latest_plan() -> None:
    occurred_at = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    state: InvestigationState = {
        "completed_steps": (
            StepResult(
                step_id="step_r1_old",
                query_key="a" * 64,
                tool_name="search_logs",
                arguments={"service": "payment-service", "query": "first round"},
                status=StepStatus.COMPLETED,
                attempts=1,
                started_at=occurred_at,
                completed_at=occurred_at,
            ),
        )
    }

    calls = OfflineEvaluationRunner._actual_tool_calls(state)

    assert calls[0].arguments == {
        "service": "payment-service",
        "query": "first round",
    }
