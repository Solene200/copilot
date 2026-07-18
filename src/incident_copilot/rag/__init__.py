"""Offline-first knowledge indexing and retrieval components."""

from incident_copilot.rag.bm25 import BM25Index
from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.embeddings import FakeEmbedding
from incident_copilot.rag.loader import KnowledgeLoadError, MarkdownDocumentLoader
from incident_copilot.rag.provider import RagKnowledgeProvider
from incident_copilot.rag.retrieval import HybridRetriever
from incident_copilot.rag.rewrite import QueryRewriter
from incident_copilot.rag.schemas import (
    DocumentType,
    KnowledgeChunk,
    KnowledgeDocument,
    MetadataFilter,
    RetrievalResult,
    SearchHit,
    SearchQuery,
)
from incident_copilot.rag.splitter import MarkdownSplitter
from incident_copilot.rag.vector_store import InMemoryVectorStore, PgVectorStore, VectorStore

__all__ = [
    "BM25Index",
    "DocumentType",
    "FakeEmbedding",
    "HybridRetriever",
    "InMemoryVectorStore",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeLoadError",
    "MarkdownDocumentLoader",
    "MarkdownSplitter",
    "MetadataFilter",
    "PgVectorStore",
    "QueryRewriter",
    "RagKnowledgeProvider",
    "RetrievalResult",
    "SearchHit",
    "SearchQuery",
    "VectorStore",
    "build_fixture_retriever",
]
