"""Idempotent ingestion and reciprocal-rank hybrid retrieval."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from incident_copilot.rag.bm25 import BM25Index
from incident_copilot.rag.embeddings import FakeEmbedding
from incident_copilot.rag.rewrite import QueryRewriter
from incident_copilot.rag.schemas import (
    EmbeddedChunk,
    IngestResult,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalResult,
    SearchHit,
    SearchQuery,
)
from incident_copilot.rag.splitter import MarkdownSplitter
from incident_copilot.rag.vector_store import VectorStore


class HybridRetriever:
    """Combine BM25 and vector candidates with citation-preserving RRF."""

    def __init__(
        self,
        *,
        splitter: MarkdownSplitter,
        embedding: FakeEmbedding,
        lexical_index: BM25Index,
        vector_store: VectorStore,
        rewriter: QueryRewriter,
        rrf_k: int = 60,
        candidate_multiplier: int = 4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if rrf_k < 1 or rrf_k > 1_000:
            raise ValueError("rrf_k must be between 1 and 1000")
        if candidate_multiplier < 1 or candidate_multiplier > 20:
            raise ValueError("candidate_multiplier must be between 1 and 20")
        self._splitter = splitter
        self._embedding = embedding
        self._lexical_index = lexical_index
        self._vector_store = vector_store
        self._rewriter = rewriter
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier
        self._clock = clock or (lambda: datetime.now(UTC))
        self._documents: dict[str, KnowledgeDocument] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def ingest(self, documents: Sequence[KnowledgeDocument]) -> IngestResult:
        """Replace supplied documents by ID and rebuild the deterministic lexical view."""
        document_ids = [document.document_id for document in documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("ingest input contains duplicate document IDs")

        for document_id in document_ids:
            self._documents.pop(document_id, None)
            for chunk_id in tuple(self._chunks):
                if self._chunks[chunk_id].document_id == document_id:
                    del self._chunks[chunk_id]
        self._vector_store.delete_documents(document_ids)

        new_chunks = self._splitter.split_documents(tuple(documents))
        for document in documents:
            self._documents[document.document_id] = document
        for chunk in new_chunks:
            self._chunks[chunk.chunk_id] = chunk

        all_chunks = tuple(sorted(self._chunks.values(), key=lambda item: item.chunk_id))
        self._lexical_index.rebuild(all_chunks)
        embeddings = self._embedding.embed_many([chunk.text for chunk in new_chunks])
        embedded_records = tuple(
            EmbeddedChunk(
                chunk=chunk,
                embedding=vector,
                embedding_model=self._embedding.model_name,
                embedding_version=self._embedding.version,
            )
            for chunk, vector in zip(new_chunks, embeddings, strict=True)
        )
        self._vector_store.upsert(embedded_records)
        return IngestResult(
            input_document_count=len(documents),
            indexed_document_count=self.document_count,
            indexed_chunk_count=self.chunk_count,
            embedded_chunk_count=len(embedded_records),
        )

    def search(self, request: SearchQuery) -> RetrievalResult:
        """Rewrite, retrieve, fuse, content-dedupe, and return bounded ranked hits."""
        rewritten = self._rewriter.rewrite(request.query)
        candidate_k = min(200, max(request.top_k * self._candidate_multiplier, 20))
        lexical = self._lexical_index.search(
            rewritten,
            top_k=candidate_k,
            metadata_filter=request.metadata_filter,
        )
        query_embedding = self._embedding.embed(rewritten)
        vector = self._vector_store.search(
            query_embedding,
            top_k=candidate_k,
            metadata_filter=request.metadata_filter,
        )

        fused_scores: defaultdict[str, float] = defaultdict(float)
        sources: defaultdict[str, set[str]] = defaultdict(set)
        chunks: dict[str, KnowledgeChunk] = {}
        for source_name, candidates in (("bm25", lexical), ("vector", vector)):
            for rank, candidate in enumerate(candidates, start=1):
                chunk_id = candidate.chunk.chunk_id
                fused_scores[chunk_id] += 1.0 / (self._rrf_k + rank)
                sources[chunk_id].add(source_name)
                chunks[chunk_id] = candidate.chunk

        ordered_ids = sorted(fused_scores, key=lambda item: (-fused_scores[item], item))
        unique_ids: list[str] = []
        seen_hashes: set[str] = set()
        for chunk_id in ordered_ids:
            content_hash = chunks[chunk_id].content_hash
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            unique_ids.append(chunk_id)
            if len(unique_ids) == request.top_k:
                break

        maximum_rrf_score = 2.0 / (self._rrf_k + 1)
        hits = tuple(
            SearchHit(
                chunk=chunks[chunk_id],
                score=min(1.0, fused_scores[chunk_id] / maximum_rrf_score),
                rank=rank,
                matched_by=tuple(sorted(sources[chunk_id])),
            )
            for rank, chunk_id in enumerate(unique_ids, start=1)
        )
        return RetrievalResult(
            original_query=request.query,
            rewritten_query=rewritten,
            hits=hits,
            indexed_document_count=self.document_count,
            indexed_chunk_count=self.chunk_count,
            retrieved_at=self._clock(),
        )
