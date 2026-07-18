"""Vector store port with in-memory and parameterized pgvector adapters."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from incident_copilot.rag.schemas import (
    EmbeddedChunk,
    MetadataFilter,
    ScoredChunk,
    chunk_matches_filter,
)


class VectorStore(Protocol):
    """Replaceable vector index contract used by HybridRetriever."""

    def delete_documents(self, document_ids: Sequence[str]) -> int:
        """Delete all vectors belonging to the supplied document IDs."""
        ...

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        """Insert or replace records by stable chunk ID."""
        ...

    def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        """Return vector candidates in descending similarity order."""
        ...


class InMemoryVectorStore:
    """Deterministic cosine vector index for default offline operation."""

    def __init__(self, *, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("vector dimension must be positive")
        self._dimension = dimension
        self._records: dict[str, EmbeddedChunk] = {}

    @property
    def size(self) -> int:
        return len(self._records)

    def delete_documents(self, document_ids: Sequence[str]) -> int:
        targets = set(document_ids)
        deleted = 0
        for chunk_id in tuple(self._records):
            if self._records[chunk_id].chunk.document_id in targets:
                del self._records[chunk_id]
                deleted += 1
        return deleted

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        for record in records:
            self._validate_dimension(record.embedding)
            self._records[record.chunk.chunk_id] = record
        return len(records)

    def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        if top_k < 1 or top_k > 200:
            raise ValueError("vector top_k must be between 1 and 200")
        self._validate_dimension(embedding)
        query_norm = math.sqrt(sum(value * value for value in embedding))
        if query_norm == 0:
            raise ValueError("query embedding must not be a zero vector")

        candidates: list[ScoredChunk] = []
        for record in self._records.values():
            if not chunk_matches_filter(record.chunk, metadata_filter):
                continue
            record_norm = math.sqrt(sum(value * value for value in record.embedding))
            dot_product = sum(
                left * right for left, right in zip(embedding, record.embedding, strict=True)
            )
            cosine = dot_product / (query_norm * record_norm) if record_norm else 0.0
            candidates.append(ScoredChunk(chunk=record.chunk, score=max(0.0, cosine)))

        candidates.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(candidates[:top_k])

    def _validate_dimension(self, embedding: Sequence[float]) -> None:
        if len(embedding) != self._dimension:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match {self._dimension}"
            )


class PgVectorSession(Protocol):
    """Minimal SQL session expected by PgVectorStore.

    A thin wrapper around psycopg/SQLAlchemy can implement this without making either package a
    default project dependency.
    """

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> None:
        """Execute a parameterized statement."""
        ...

    def fetch_all(
        self, statement: str, parameters: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        """Execute a parameterized query and return mapping rows."""
        ...


class PgVectorStore:
    """Explicit pgvector SQL adapter; schema creation is never implicit."""

    def __init__(
        self, session: PgVectorSession, *, dimension: int, table: str = "knowledge_chunks"
    ):
        if dimension < 1 or dimension > 4_096:
            raise ValueError("pgvector dimension must be between 1 and 4096")
        if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table) is None:
            raise ValueError("pgvector table name must be a safe SQL identifier")
        self._session = session
        self._dimension = dimension
        self._table = table

    def ensure_schema(self) -> None:
        """Explicitly install the extension/table when the caller has migration authority."""
        self._session.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self._session.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                service_tags TEXT[] NOT NULL,
                environment_tags TEXT[] NOT NULL,
                document_type TEXT NOT NULL,
                effective_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                embedding VECTOR({self._dimension}) NOT NULL
            )
            """.strip()
        )

    def delete_documents(self, document_ids: Sequence[str]) -> int:
        if not document_ids:
            return 0
        self._session.execute(
            f"DELETE FROM {self._table} WHERE document_id = ANY(%s::text[])",
            (list(document_ids),),
        )
        return len(document_ids)

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        statement = f"""
            INSERT INTO {self._table} (
                chunk_id, document_id, content_hash, service_tags, environment_tags,
                document_type, effective_at, payload, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                content_hash = EXCLUDED.content_hash,
                service_tags = EXCLUDED.service_tags,
                environment_tags = EXCLUDED.environment_tags,
                document_type = EXCLUDED.document_type,
                effective_at = EXCLUDED.effective_at,
                payload = EXCLUDED.payload,
                embedding = EXCLUDED.embedding
        """.strip()
        for record in records:
            self._validate_dimension(record.embedding)
            chunk = record.chunk
            self._session.execute(
                statement,
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.content_hash,
                    list(chunk.service_tags),
                    list(chunk.environment_tags),
                    chunk.document_type.value,
                    chunk.effective_at,
                    json.dumps(record.model_dump(mode="json"), separators=(",", ":")),
                    self._vector_literal(record.embedding),
                ),
            )
        return len(records)

    def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        if top_k < 1 or top_k > 200:
            raise ValueError("pgvector top_k must be between 1 and 200")
        self._validate_dimension(embedding)
        vector = self._vector_literal(embedding)
        clauses: list[str] = []
        filter_parameters: list[object] = []
        if metadata_filter.services:
            clauses.append("service_tags && %s::text[]")
            filter_parameters.append(list(metadata_filter.services))
        if metadata_filter.environments:
            clauses.append("environment_tags && %s::text[]")
            filter_parameters.append(list(metadata_filter.environments))
        if metadata_filter.document_types:
            clauses.append("document_type = ANY(%s::text[])")
            filter_parameters.append([item.value for item in metadata_filter.document_types])
        if metadata_filter.effective_before is not None:
            clauses.append("effective_at < %s")
            filter_parameters.append(metadata_filter.effective_before)
        if metadata_filter.effective_after is not None:
            clauses.append("effective_at > %s")
            filter_parameters.append(metadata_filter.effective_after)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        statement = (
            f"SELECT payload, GREATEST(0, 1 - (embedding <=> %s::vector)) AS score "
            f"FROM {self._table}{where} "
            "ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        parameters = (vector, *filter_parameters, vector, top_k)
        rows = self._session.fetch_all(statement, parameters)
        candidates: list[ScoredChunk] = []
        for row in rows:
            payload = row.get("payload")
            record = (
                EmbeddedChunk.model_validate_json(payload)
                if isinstance(payload, str)
                else EmbeddedChunk.model_validate(payload)
            )
            score_value = row.get("score", 0.0)
            if not isinstance(score_value, int | float):
                raise ValueError("pgvector score must be numeric")
            candidates.append(ScoredChunk(chunk=record.chunk, score=max(0.0, score_value)))
        return tuple(candidates)

    def _validate_dimension(self, embedding: Sequence[float]) -> None:
        if len(embedding) != self._dimension:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match {self._dimension}"
            )

    @staticmethod
    def _vector_literal(embedding: Sequence[float]) -> str:
        return "[" + ",".join(format(value, ".17g") for value in embedding) + "]"
