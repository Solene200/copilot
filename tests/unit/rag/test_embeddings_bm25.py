"""Tests for deterministic fake embeddings and lexical ranking."""

import math
from pathlib import Path

from incident_copilot.rag.bm25 import BM25Index
from incident_copilot.rag.embeddings import FakeEmbedding
from incident_copilot.rag.loader import MarkdownDocumentLoader
from incident_copilot.rag.schemas import DocumentType, KnowledgeChunk, MetadataFilter
from incident_copilot.rag.splitter import MarkdownSplitter


def knowledge_chunks() -> tuple[KnowledgeChunk, ...]:
    root = Path(__file__).parents[3] / "data" / "knowledge"
    documents = MarkdownDocumentLoader(root).load()
    return MarkdownSplitter().split_documents(documents)


def test_fake_embedding_is_deterministic_versioned_and_unit_length() -> None:
    embedding = FakeEmbedding(dimension=32)

    first = embedding.embed("database connection pool timeout")
    second = embedding.embed("database connection pool timeout")
    different = embedding.embed("external payment gateway latency")

    assert first == second
    assert first != different
    assert len(first) == 32
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)
    assert embedding.model_name == "fake-signed-hash"
    assert embedding.version == "1"


def test_bm25_ranks_relevant_runbook_and_applies_metadata_filter() -> None:
    index = BM25Index()
    chunks = knowledge_chunks()

    first_size = index.rebuild(chunks)
    second_size = index.rebuild(chunks)
    runbooks = index.search(
        "database connection pool timeout",
        top_k=5,
        metadata_filter=MetadataFilter(
            services=("payment-service",),
            document_types=(DocumentType.RUNBOOK,),
        ),
    )
    gateway = index.search(
        "gateway latency",
        top_k=5,
        metadata_filter=MetadataFilter(services=("payment-gateway",)),
    )

    assert first_size == second_size == len(chunks)
    assert runbooks[0].chunk.document_id == "doc_runbook_payment_db_pool"
    assert all(item.chunk.document_type is DocumentType.RUNBOOK for item in runbooks)
    assert all("payment-service" in item.chunk.service_tags for item in runbooks)
    assert gateway[0].chunk.document_id == "doc_runbook_payment_gateway_latency"


def test_bm25_returns_empty_for_no_terms_or_filtered_out_corpus() -> None:
    index = BM25Index()
    index.rebuild(knowledge_chunks())

    assert index.search("???", top_k=5, metadata_filter=MetadataFilter()) == ()
    assert (
        index.search(
            "database pool",
            top_k=5,
            metadata_filter=MetadataFilter(services=("unknown-service",)),
        )
        == ()
    )
