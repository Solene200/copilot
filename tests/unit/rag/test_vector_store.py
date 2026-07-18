"""Contract tests for in-memory and parameterized pgvector stores."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from incident_copilot.rag.embeddings import FakeEmbedding
from incident_copilot.rag.loader import MarkdownDocumentLoader
from incident_copilot.rag.schemas import (
    DocumentType,
    EmbeddedChunk,
    MetadataFilter,
)
from incident_copilot.rag.splitter import MarkdownSplitter
from incident_copilot.rag.vector_store import InMemoryVectorStore, PgVectorStore


def embedded_records() -> tuple[EmbeddedChunk, ...]:
    root = Path(__file__).parents[3] / "data" / "knowledge"
    documents = MarkdownDocumentLoader(root).load()
    chunks = MarkdownSplitter().split_documents(documents)
    embedding = FakeEmbedding(dimension=32)
    return tuple(
        EmbeddedChunk(
            chunk=chunk,
            embedding=embedding.embed(chunk.text),
            embedding_model=embedding.model_name,
            embedding_version=embedding.version,
        )
        for chunk in chunks
    )


class RecordingSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[Mapping[str, object]] = []

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> None:
        self.executed.append((statement, tuple(parameters)))

    def fetch_all(
        self, statement: str, parameters: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.fetched.append((statement, tuple(parameters)))
        return self.rows


def test_in_memory_vector_store_upsert_search_filter_delete_and_dimension() -> None:
    records = embedded_records()
    embedding = FakeEmbedding(dimension=32)
    store = InMemoryVectorStore(dimension=32)

    assert store.upsert(records) == len(records)
    assert store.upsert(records) == len(records)
    assert store.size == len(records)
    results = store.search(
        embedding.embed("database connection pool timeout"),
        top_k=5,
        metadata_filter=MetadataFilter(
            services=("payment-service",),
            document_types=(DocumentType.RUNBOOK,),
        ),
    )

    assert results
    assert all(item.score >= 0 for item in results)
    assert all(item.chunk.document_type is DocumentType.RUNBOOK for item in results)
    deleted = store.delete_documents(("doc_runbook_payment_gateway_latency",))
    assert deleted > 0
    assert store.size == len(records) - deleted
    with pytest.raises(ValueError, match="dimension"):
        store.search((1.0, 2.0), top_k=1, metadata_filter=MetadataFilter())


def test_pgvector_adapter_uses_explicit_schema_and_parameterized_queries() -> None:
    session = RecordingSession()
    store = PgVectorStore(session, dimension=32, table="knowledge_chunks_test")
    record = embedded_records()[0]

    store.ensure_schema()
    assert store.upsert((record,)) == 1
    store.delete_documents((record.chunk.document_id,))
    session.rows = [
        {
            "payload": json.dumps(record.model_dump(mode="json")),
            "score": 0.75,
        }
    ]
    results = store.search(
        record.embedding,
        top_k=3,
        metadata_filter=MetadataFilter(
            services=("payment-service",),
            environments=("production",),
            document_types=(DocumentType.RUNBOOK,),
            effective_before=record.chunk.effective_at.replace(year=2027),
        ),
    )

    assert "CREATE EXTENSION IF NOT EXISTS vector" in session.executed[0][0]
    assert "VECTOR(32)" in session.executed[1][0]
    insert_statement, insert_parameters = session.executed[2]
    assert "ON CONFLICT (chunk_id) DO UPDATE" in insert_statement
    assert record.chunk.chunk_id in insert_parameters
    search_statement, search_parameters = session.fetched[0]
    assert "service_tags && %s::text[]" in search_statement
    assert "environment_tags && %s::text[]" in search_statement
    assert "document_type = ANY(%s::text[])" in search_statement
    assert search_parameters[-1] == 3
    assert results[0].chunk == record.chunk
    assert results[0].score == 0.75


def test_pgvector_adapter_rejects_unsafe_table_and_wrong_dimension() -> None:
    session = RecordingSession()
    with pytest.raises(ValueError, match="safe SQL identifier"):
        PgVectorStore(session, dimension=32, table="chunks; DROP TABLE incidents")

    store = PgVectorStore(session, dimension=32)
    with pytest.raises(ValueError, match="dimension"):
        store.search((1.0,), top_k=1, metadata_filter=MetadataFilter())
