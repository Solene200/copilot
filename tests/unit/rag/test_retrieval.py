"""Tests for query rewrite, idempotent ingest, RRF, dedupe, and RAG provider."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.loader import MarkdownDocumentLoader
from incident_copilot.rag.provider import RagKnowledgeProvider
from incident_copilot.rag.rewrite import QueryRewriter
from incident_copilot.rag.schemas import DocumentType, MetadataFilter, SearchQuery
from incident_copilot.tools.schemas import (
    QueryContext,
    SearchRunbooksInput,
    SearchSimilarIncidentsInput,
)

FIXED_NOW = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)


def knowledge_root() -> Path:
    return Path(__file__).parents[3] / "data" / "knowledge"


def test_query_rewrite_is_transparent_deterministic_and_bilingual() -> None:
    rewriter = QueryRewriter()

    first = rewriter.rewrite("支付服务 数据库 连接池 超时")
    second = rewriter.rewrite("支付服务 数据库 连接池 超时")

    assert first == second
    assert "payment-service" in first
    assert "database" in first
    assert "connection" in first
    assert "pool" in first
    assert "timeout" in first


def test_ingest_and_hybrid_search_are_idempotent_and_citation_preserving() -> None:
    retriever, initial = build_fixture_retriever(clock=lambda: FIXED_NOW)
    documents = MarkdownDocumentLoader(knowledge_root()).load()
    first = retriever.search(
        SearchQuery(
            query="database connection pool timeout configuration",
            top_k=5,
            metadata_filter=MetadataFilter(
                services=("payment-service",),
                document_types=(DocumentType.RUNBOOK,),
            ),
        )
    )

    repeated_ingest = retriever.ingest(documents)
    second = retriever.search(
        SearchQuery(
            query="database connection pool timeout configuration",
            top_k=5,
            metadata_filter=MetadataFilter(
                services=("payment-service",),
                document_types=(DocumentType.RUNBOOK,),
            ),
        )
    )

    assert initial.indexed_document_count == repeated_ingest.indexed_document_count == 4
    assert initial.indexed_chunk_count == repeated_ingest.indexed_chunk_count
    assert first == second
    assert first.hits[0].chunk.document_id == "doc_runbook_payment_db_pool"
    assert all(hit.chunk.citation.uri.startswith("internal://knowledge/") for hit in first.hits)
    assert all(hit.matched_by for hit in first.hits)
    assert len(first.hits) <= 5


def test_hybrid_search_applies_metadata_filter_top_k_and_empty_result() -> None:
    retriever, _ = build_fixture_retriever(clock=lambda: FIXED_NOW)

    incidents = retriever.search(
        SearchQuery(
            query="connection limit reduction incident",
            top_k=2,
            metadata_filter=MetadataFilter(
                services=("payment-service",),
                document_types=(DocumentType.INCIDENT,),
                effective_before=datetime(2026, 7, 18, tzinfo=UTC),
            ),
        )
    )
    empty = retriever.search(
        SearchQuery(
            query="database connection pool",
            top_k=3,
            metadata_filter=MetadataFilter(services=("unknown-service",)),
        )
    )

    assert len(incidents.hits) <= 2
    assert incidents.hits[0].chunk.document_id == "doc_incident_payment_pool_20260628"
    assert all(hit.chunk.document_type is DocumentType.INCIDENT for hit in incidents.hits)
    assert empty.hits == ()


def test_hybrid_search_deduplicates_equal_chunk_content_hashes() -> None:
    retriever, _ = build_fixture_retriever(clock=lambda: FIXED_NOW)
    original = MarkdownDocumentLoader(knowledge_root()).load()[0]
    duplicate = original.model_copy(
        update={
            "document_id": "doc_duplicate_content",
            "source_uri": "internal://knowledge/duplicate.md",
        }
    )

    retriever.ingest((duplicate,))
    result = retriever.search(SearchQuery(query="connection pool timeout", top_k=20))
    hashes = [hit.chunk.content_hash for hit in result.hits]

    assert len(hashes) == len(set(hashes))


@pytest.mark.asyncio
async def test_rag_provider_returns_tool_compatible_evidence() -> None:
    retriever, _ = build_fixture_retriever(clock=lambda: FIXED_NOW)
    provider = RagKnowledgeProvider(retriever)
    context = QueryContext(
        correlation_id="rag-provider-test",
        deadline=datetime(2026, 7, 18, 3, 1, tzinfo=UTC),
        remaining_tool_calls=5,
    )

    runbooks = await provider.search_runbooks(
        SearchRunbooksInput(
            service="payment-service",
            query="database connection pool timeout",
            limit=3,
        ),
        context,
    )
    incidents = await provider.search_similar_incidents(
        SearchSimilarIncidentsInput(
            service="payment-service",
            query="connection limit incident",
            before_time=datetime(2026, 7, 18, tzinfo=UTC),
            lookback_days=90,
            limit=3,
        ),
        context,
    )

    assert runbooks[0].source_name == "hybrid-knowledge"
    assert runbooks[0].citation.uri.startswith("internal://knowledge/runbooks/")
    assert runbooks[0].service == "payment-service"
    assert incidents[0].metadata["document_type"] == "incident"
    assert incidents[0].citation.uri.startswith("internal://knowledge/incidents/")
