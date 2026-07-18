"""Deterministic local embedding used to verify retrieval data flow."""

import hashlib
import math
from collections.abc import Sequence

from incident_copilot.rag.splitter import tokenize


class FakeEmbedding:
    """Versioned signed hash embedding with no model or network dependency.

    It is intentionally not presented as a semantic-quality embedding. Query rewrite and
    lexical retrieval carry most of the fixture relevance signal.
    """

    model_name = "fake-signed-hash"
    version = "1"

    def __init__(self, *, dimension: int = 64) -> None:
        if dimension < 16 or dimension > 4_096:
            raise ValueError("fake embedding dimension must be between 16 and 4096")
        self.dimension = dimension

    def embed(self, text: str) -> tuple[float, ...]:
        """Map normalized tokens to a stable unit-length signed hash vector."""
        tokens = tokenize(text)
        if not tokens:
            raise ValueError("cannot embed text without supported tokens")
        values = [0.0] * self.dimension
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            values[bucket] += sign
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("fake embedding produced a zero vector")
        return tuple(value / norm for value in values)

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Embed a stable sequence without batching side effects."""
        return tuple(self.embed(text) for text in texts)
