"""Small deterministic BM25 index for offline fixture retrieval."""

import math
from collections import Counter
from collections.abc import Sequence

from incident_copilot.rag.schemas import (
    KnowledgeChunk,
    MetadataFilter,
    ScoredChunk,
    chunk_matches_filter,
)
from incident_copilot.rag.splitter import tokenize


class BM25Index:
    """In-memory BM25 implementation with stable tie-breaking and metadata filtering."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        self._k1 = k1
        self._b = b
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._term_frequencies: dict[str, Counter[str]] = {}
        self._document_frequencies: Counter[str] = Counter()
        self._document_lengths: dict[str, int] = {}
        self._average_length = 0.0

    @property
    def size(self) -> int:
        return len(self._chunks)

    def rebuild(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """Replace the index, de-duplicating identical chunk IDs deterministically."""
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._term_frequencies.clear()
        self._document_frequencies.clear()
        self._document_lengths.clear()

        for chunk_id, chunk in self._chunks.items():
            terms = tokenize(chunk.text)
            frequencies = Counter(terms)
            self._term_frequencies[chunk_id] = frequencies
            self._document_lengths[chunk_id] = len(terms)
            self._document_frequencies.update(frequencies.keys())

        total_length = sum(self._document_lengths.values())
        self._average_length = total_length / len(self._chunks) if self._chunks else 0.0
        return self.size

    def search(
        self,
        query: str,
        *,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        """Return positive-scoring lexical candidates after metadata filtering."""
        if top_k < 1 or top_k > 200:
            raise ValueError("BM25 top_k must be between 1 and 200")
        query_terms = tuple(dict.fromkeys(tokenize(query)))
        if not query_terms or not self._chunks:
            return ()

        candidates: list[ScoredChunk] = []
        corpus_size = len(self._chunks)
        for chunk_id, chunk in self._chunks.items():
            if not chunk_matches_filter(chunk, metadata_filter):
                continue
            frequencies = self._term_frequencies[chunk_id]
            length = self._document_lengths[chunk_id]
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                document_frequency = self._document_frequencies[term]
                inverse_document_frequency = math.log(
                    1 + (corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                normalization = frequency + self._k1 * (
                    1 - self._b + self._b * length / max(self._average_length, 1.0)
                )
                score += inverse_document_frequency * frequency * (self._k1 + 1) / normalization
            if score > 0:
                candidates.append(ScoredChunk(chunk=chunk, score=score))

        candidates.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(candidates[:top_k])
