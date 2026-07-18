"""Offline end-to-end tests from repository documents through Phase 2 tools."""

from datetime import UTC, datetime, timedelta

import pytest

from incident_copilot.rag import RagKnowledgeProvider, SearchQuery, build_fixture_retriever
from incident_copilot.tools import (
    FixtureProvider,
    ProviderBundle,
    QueryContext,
    build_tool_registry,
)


def make_context() -> QueryContext:
    return QueryContext(
        correlation_id="rag-tool-integration",
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        remaining_tool_calls=10,
    )


@pytest.mark.asyncio
async def test_hybrid_rag_integrates_with_both_knowledge_tools() -> None:
    retriever, ingest = build_fixture_retriever(
        clock=lambda: datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    )
    rag_provider = RagKnowledgeProvider(retriever)
    fixture_provider = FixtureProvider.payment_service()
    registry = build_tool_registry(
        ProviderBundle(
            logs=fixture_provider,
            metrics=fixture_provider,
            traces=fixture_provider,
            changes=fixture_provider,
            topology=fixture_provider,
            knowledge=rag_provider,
        ),
        retry_backoff_seconds=0,
    )

    runbooks = await registry.execute(
        "search_runbooks",
        {
            "service": "payment-service",
            "query": "database connection pool timeout",
            "limit": 3,
        },
        make_context(),
    )
    incidents = await registry.execute(
        "search_similar_incidents",
        {
            "service": "payment-service",
            "query": "connection limit incident",
            "before_time": "2026-07-18T10:00:00+08:00",
            "lookback_days": 90,
            "limit": 3,
        },
        make_context(),
    )

    assert ingest.indexed_document_count == 4
    assert ingest.indexed_chunk_count > ingest.indexed_document_count
    assert runbooks.evidence[0].metadata["document_id"] == "doc_runbook_payment_db_pool"
    assert incidents.evidence[0].metadata["document_id"] == "doc_incident_payment_pool_20260628"
    assert all(item.citation.uri.startswith("internal://knowledge/") for item in runbooks.evidence)


def test_fixed_retrieval_regression_queries_report_actual_fixture_metrics() -> None:
    retriever, _ = build_fixture_retriever(clock=lambda: datetime(2026, 7, 18, 3, 0, tzinfo=UTC))
    cases = (
        ("database connection pool timeout", "doc_runbook_payment_db_pool"),
        ("payment-service dependencies health endpoint", "doc_service_payment_service"),
        ("historical incident connection limit reduction", "doc_incident_payment_pool_20260628"),
    )
    reciprocal_ranks: list[float] = []
    observed_rankings: list[tuple[str, ...]] = []
    recalled = 0
    for query, expected_document_id in cases:
        result = retriever.search(SearchQuery(query=query, top_k=3))
        ranked_documents = [hit.chunk.document_id for hit in result.hits]
        observed_rankings.append(tuple(ranked_documents))
        if expected_document_id in ranked_documents:
            recalled += 1
            reciprocal_ranks.append(1.0 / (ranked_documents.index(expected_document_id) + 1))
        else:
            reciprocal_ranks.append(0.0)

    recall_at_3 = recalled / len(cases)
    mean_reciprocal_rank = sum(reciprocal_ranks) / len(reciprocal_ranks)

    assert observed_rankings == [
        (
            "doc_service_payment_service",
            "doc_incident_payment_pool_20260628",
            "doc_runbook_payment_db_pool",
        ),
        (
            "doc_service_payment_service",
            "doc_incident_payment_pool_20260628",
            "doc_runbook_payment_db_pool",
        ),
        (
            "doc_incident_payment_pool_20260628",
            "doc_incident_payment_pool_20260628",
            "doc_runbook_payment_db_pool",
        ),
    ]
    assert recall_at_3 == 1.0
    assert mean_reciprocal_rank == 7 / 9
