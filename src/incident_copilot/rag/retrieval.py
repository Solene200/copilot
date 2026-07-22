"""幂等索引和基于倒数排名融合的混合检索。

本模块同时编排知识写入与查询。写入时按 document ID 原子替换 Chunk;
查询时对同一改写查询执行 BM25 和向量召回, 再用 RRF 融合、content hash 去重并保留
原始 Citation。默认实现完全本地且确定性可复现。
"""

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
    """使用保留引用的 RRF 融合 BM25 与向量检索候选。

    BM25 原始分数与 cosine 相似度不在同一量纲,因此不直接相加。RRF 只使用各
    检索器中的排名, 形成更容易解释和稳定测试的混合基线。
    """

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
        """按 ID 替换输入文档,并重建确定性的词法索引视图。

        输入文档先切分和 embedding。只有全部新记录准备成功后才替换向量后端并
        更新内存文档/Chunk 映射, 失败不会留下半更新的 Retriever 状态。
        """
        document_ids = [document.document_id for document in documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("ingest input contains duplicate document IDs")

        # 所有切分与向量先在临时变量中完成; 任一步失败都不会修改现有索引。
        new_chunks = self._splitter.split_documents(tuple(documents))
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

        updated_documents = dict(self._documents)
        # 同 document ID 的旧 Chunk 全部移除,避免文档更新后残留过期向量。
        updated_chunks = {
            chunk_id: chunk
            for chunk_id, chunk in self._chunks.items()
            if chunk.document_id not in document_ids
        }
        for document in documents:
            updated_documents[document.document_id] = document
        for chunk in new_chunks:
            updated_chunks[chunk.chunk_id] = chunk

        # 向量后端先原子替换目标文档, 成功后才发布新的词法和内存视图。
        self._vector_store.replace_documents(document_ids, embedded_records)
        all_chunks = tuple(sorted(updated_chunks.values(), key=lambda item: item.chunk_id))
        self._lexical_index.rebuild(all_chunks)
        self._documents = updated_documents
        self._chunks = updated_chunks
        return IngestResult(
            input_document_count=len(documents),
            indexed_document_count=self.document_count,
            indexed_chunk_count=self.chunk_count,
            embedded_chunk_count=len(embedded_records),
        )

    def search(self, request: SearchQuery) -> RetrievalResult:
        """依次执行改写、召回、融合和内容去重,返回有界排序结果。

        读取 query/top_k/metadata filter;返回 original/rewritten query、排序命中和
        索引规模。Citation 始终来自原 KnowledgeChunk, 不由检索器重新编造。
        """
        # 改写只扩展已知缩写和短语, 原始查询仍保留在返回结果中用于审计。
        rewritten = self._rewriter.rewrite(request.query)
        # 先扩大候选池再融合,最终仍严格裁剪到用户请求的 top_k。
        candidate_k = min(200, max(request.top_k * self._candidate_multiplier, 20))
        lexical = self._lexical_index.search(
            rewritten,
            top_k=candidate_k,
            metadata_filter=request.metadata_filter,
        )
        query_embedding = self._embedding.embed(rewritten)
        vector = self._vector_store.search(
            query_embedding,
            embedding_model=self._embedding.model_name,
            embedding_version=self._embedding.version,
            top_k=candidate_k,
            metadata_filter=request.metadata_filter,
        )

        fused_scores: defaultdict[str, float] = defaultdict(float)
        sources: defaultdict[str, set[str]] = defaultdict(set)
        chunks: dict[str, KnowledgeChunk] = {}
        for source_name, candidates in (("bm25", lexical), ("vector", vector)):
            for rank, candidate in enumerate(candidates, start=1):
                chunk_id = candidate.chunk.chunk_id
                # RRF 只累加名次倒数, 避免直接比较 BM25 分数和余弦相似度这两种量纲。
                fused_scores[chunk_id] += 1.0 / (self._rrf_k + rank)
                sources[chunk_id].add(source_name)
                chunks[chunk_id] = candidate.chunk

        # 使用稳定 chunk_id 作为同分 tie-break,保证离线回归结果可复现。
        ordered_ids = sorted(fused_scores, key=lambda item: (-fused_scores[item], item))
        unique_ids: list[str] = []
        seen_hashes: set[str] = set()
        for chunk_id in ordered_ids:
            content_hash = chunks[chunk_id].content_hash
            # 不同 Chunk ID 若内容相同只保留排名更高者,同时保留其原始引用。
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            unique_ids.append(chunk_id)
            if len(unique_ids) == request.top_k:
                break

        # 两路都排名第一是理论最大分, 用它把展示分数规范到 0 到 1。
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
