"""Adapter exposing hybrid RAG through the Phase 2 KnowledgeProvider port."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.rag.retrieval import HybridRetriever
from incident_copilot.rag.schemas import DocumentType, MetadataFilter, SearchHit, SearchQuery
from incident_copilot.tools.schemas import (
    QueryContext,
    SearchRunbooksInput,
    SearchSimilarIncidentsInput,
)


class RagKnowledgeProvider:
    """Convert citation-preserving hybrid hits into the shared Evidence contract."""

    def __init__(self, retriever: HybridRetriever) -> None:
        self._retriever = retriever

    async def search_runbooks(
        self, query: SearchRunbooksInput, context: QueryContext
    ) -> Sequence[Evidence]:
        del context
        result = self._retriever.search(
            SearchQuery(
                query=query.query,
                top_k=query.limit,
                metadata_filter=MetadataFilter(
                    services=(query.service,),
                    document_types=(DocumentType.RUNBOOK,),
                ),
            )
        )
        return self._to_evidence(
            result.hits, service=query.service, collected_at=result.retrieved_at
        )

    async def search_similar_incidents(
        self, query: SearchSimilarIncidentsInput, context: QueryContext
    ) -> Sequence[Evidence]:
        del context
        result = self._retriever.search(
            SearchQuery(
                query=query.query,
                top_k=query.limit,
                metadata_filter=MetadataFilter(
                    services=(query.service,),
                    document_types=(DocumentType.INCIDENT,),
                    effective_before=query.before_time,
                    effective_after=query.before_time - timedelta(days=query.lookback_days),
                ),
            )
        )
        return self._to_evidence(
            result.hits, service=query.service, collected_at=result.retrieved_at
        )

    @staticmethod
    def _to_evidence(
        hits: Sequence[SearchHit], *, service: str, collected_at: datetime
    ) -> tuple[Evidence, ...]:
        evidence: list[Evidence] = []
        for hit in hits:
            chunk = hit.chunk
            evidence.append(
                Evidence(
                    evidence_id=f"ev_knowledge_{chunk.content_hash[:24]}",
                    source_type=SourceType.KNOWLEDGE,
                    source_name="hybrid-knowledge",
                    title=chunk.document_title,
                    content=chunk.text,
                    summary=chunk.text[:1_000],
                    timestamp=chunk.effective_at,
                    service=service,
                    relevance_score=hit.score,
                    reliability_score=(
                        0.9 if chunk.document_type is DocumentType.RUNBOOK else 0.85
                    ),
                    metadata={
                        "document_id": chunk.document_id,
                        "document_type": chunk.document_type.value,
                        "chunk_id": chunk.chunk_id,
                        "matched_by": list(hit.matched_by),
                    },
                    citation=chunk.citation,
                    content_hash=chunk.content_hash,
                    collected_at=collected_at,
                )
            )
        return tuple(evidence)
