"""具有内存和参数化 pgvector Adapter 的向量存储端口。"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from incident_copilot.rag.schemas import (
    EmbeddedChunk,
    MetadataFilter,
    ScoredChunk,
    chunk_matches_filter,
)


class VectorStore(Protocol):
    """HybridRetriever 使用的可替换向量索引契约。"""

    def delete_documents(self, document_ids: Sequence[str]) -> int:
        """删除属于指定文档 ID 的全部向量。"""
        ...

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        """按稳定 Chunk ID 插入或替换记录。"""
        ...

    def replace_documents(
        self,
        document_ids: Sequence[str],
        records: Sequence[EmbeddedChunk],
    ) -> int:
        """原子替换属于指定文档的全部向量。"""
        ...

    def search(
        self,
        embedding: Sequence[float],
        *,
        embedding_model: str,
        embedding_version: str,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        """按相似度降序返回向量候选。"""
        ...


class InMemoryVectorStore:
    """默认离线操作使用的确定性余弦向量索引。"""

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
        # 遍历键快照, 删除底层字典时不会触发“迭代期间大小变化”。
        for chunk_id in tuple(self._records):
            if self._records[chunk_id].chunk.document_id in targets:
                del self._records[chunk_id]
                deleted += 1
        return deleted

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        checked = tuple(records)
        # 先验证全部向量, 任何一条非法时都不发布部分更新。
        for record in checked:
            self._validate_vector(record.embedding)
        # 写入副本后一次替换引用, 读者不会观察到处理中间状态。
        updated = dict(self._records)
        for record in checked:
            updated[record.chunk.chunk_id] = record
        self._records = updated
        return len(checked)

    def replace_documents(
        self,
        document_ids: Sequence[str],
        records: Sequence[EmbeddedChunk],
    ) -> int:
        checked = tuple(records)
        # replace 先做完整预校验, 再在临时字典中删除旧文档并写入新版本。
        for record in checked:
            self._validate_vector(record.embedding)
        targets = set(document_ids)
        updated = {
            chunk_id: record
            for chunk_id, record in self._records.items()
            if record.chunk.document_id not in targets
        }
        for record in checked:
            updated[record.chunk.chunk_id] = record
        self._records = updated
        return len(checked)

    def search(
        self,
        embedding: Sequence[float],
        *,
        embedding_model: str,
        embedding_version: str,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        if top_k < 1 or top_k > 200:
            raise ValueError("vector top_k must be between 1 and 200")
        self._validate_vector(embedding)
        query_norm = math.sqrt(sum(value * value for value in embedding))

        candidates: list[ScoredChunk] = []
        for record in self._records.values():
            # 不混搜不同模型或版本生成的向量, 因为它们不处于同一向量空间。
            if (
                record.embedding_model != embedding_model
                or record.embedding_version != embedding_version
            ):
                continue
            if not chunk_matches_filter(record.chunk, metadata_filter):
                continue
            record_norm = math.sqrt(sum(value * value for value in record.embedding))
            # 查询和记录通过点积/模长计算余弦相似度, 只保留正相关候选。
            dot_product = sum(
                left * right for left, right in zip(embedding, record.embedding, strict=True)
            )
            cosine = dot_product / (query_norm * record_norm) if record_norm else 0.0
            if cosine > 0:
                candidates.append(ScoredChunk(chunk=record.chunk, score=cosine))

        candidates.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(candidates[:top_k])

    def _validate_vector(self, embedding: Sequence[float]) -> None:
        # 维度、有限值和非零向量是余弦计算的三个必要前置条件。
        if len(embedding) != self._dimension:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match {self._dimension}"
            )
        if any(not math.isfinite(value) for value in embedding):
            raise ValueError("embedding values must be finite")
        if not any(value != 0 for value in embedding):
            raise ValueError("embedding must not be a zero vector")


class PgVectorSession(Protocol):
    """PgVectorStore 所需的最小 SQL Session。

    可以通过 psycopg 或 SQLAlchemy 的轻量 Wrapper 实现该接口,而无需把任一包设为项目
    默认依赖。
    """

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> None:
        """执行参数化语句。"""
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """返回操作失败时会回滚的事务边界。"""
        ...

    def fetch_all(
        self, statement: str, parameters: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        """执行参数化查询并返回映射行。"""
        ...


class PgVectorStore:
    """显式 pgvector SQL Adapter,绝不隐式创建 Schema。"""

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

    def delete_documents(self, document_ids: Sequence[str]) -> int:
        if not document_ids:
            return 0
        self._session.execute(
            f"DELETE FROM {self._table} WHERE document_id = ANY(%s::text[])",
            (list(document_ids),),
        )
        return len(document_ids)

    def upsert(self, records: Sequence[EmbeddedChunk]) -> int:
        checked = tuple(records)
        # 在发送任何 SQL 前验证整批维度和有限值, 降低事务内部分失败概率。
        for record in checked:
            self._validate_vector(record.embedding)
        statement = f"""
            INSERT INTO {self._table} (
                chunk_id, document_id, content_hash, service_tags, environment_tags,
                document_type, effective_at, embedding_model, embedding_version,
                payload, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                content_hash = EXCLUDED.content_hash,
                service_tags = EXCLUDED.service_tags,
                environment_tags = EXCLUDED.environment_tags,
                document_type = EXCLUDED.document_type,
                effective_at = EXCLUDED.effective_at,
                embedding_model = EXCLUDED.embedding_model,
                embedding_version = EXCLUDED.embedding_version,
                payload = EXCLUDED.payload,
                embedding = EXCLUDED.embedding
        """.strip()
        # 所有动态值都使用参数绑定; 只有初始化时校验过的表名进入 SQL 文本。
        for record in checked:
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
                    record.embedding_model,
                    record.embedding_version,
                    json.dumps(record.model_dump(mode="json"), separators=(",", ":")),
                    self._vector_literal(record.embedding),
                ),
            )
        return len(checked)

    def replace_documents(
        self,
        document_ids: Sequence[str],
        records: Sequence[EmbeddedChunk],
    ) -> int:
        # 删除旧 Chunk 与写入新 Chunk 必须在同一事务中, 任一步失败都由 Session 回滚。
        with self._session.transaction():
            self.delete_documents(document_ids)
            return self.upsert(records)

    def search(
        self,
        embedding: Sequence[float],
        *,
        embedding_model: str,
        embedding_version: str,
        top_k: int,
        metadata_filter: MetadataFilter,
    ) -> tuple[ScoredChunk, ...]:
        if top_k < 1 or top_k > 200:
            raise ValueError("pgvector top_k must be between 1 and 200")
        self._validate_vector(embedding)
        vector = self._vector_literal(embedding)
        clauses = ["embedding_model = %s", "embedding_version = %s"]
        filter_parameters: list[object] = [embedding_model, embedding_version]
        # metadata filter 被翻译为参数化 SQL 条件, 不在 Python 侧拉取全表后过滤。
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
            # 数据库 payload 仍是不可信输入, 必须重新通过 EmbeddedChunk Schema。
            payload = row.get("payload")
            record = (
                EmbeddedChunk.model_validate_json(payload)
                if isinstance(payload, str)
                else EmbeddedChunk.model_validate(payload)
            )
            if (
                record.embedding_model != embedding_model
                or record.embedding_version != embedding_version
            ):
                raise ValueError("pgvector row embedding identity does not match the query")
            score_value = row.get("score", 0.0)
            if (
                not isinstance(score_value, int | float)
                or isinstance(score_value, bool)
                or not math.isfinite(score_value)
            ):
                raise ValueError("pgvector score must be numeric")
            # Adapter 再校验数据库计算出的分数, 防止 NaN 或非数字进入融合排序。
            if score_value > 0:
                candidates.append(ScoredChunk(chunk=record.chunk, score=score_value))
        return tuple(candidates)

    def _validate_vector(self, embedding: Sequence[float]) -> None:
        # 在构造 pgvector 字面量前拒绝维度错误、NaN、无穷值和零向量。
        if len(embedding) != self._dimension:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match {self._dimension}"
            )
        if any(not math.isfinite(value) for value in embedding):
            raise ValueError("embedding values must be finite")
        if not any(value != 0 for value in embedding):
            raise ValueError("embedding must not be a zero vector")

    @staticmethod
    def _vector_literal(embedding: Sequence[float]) -> str:
        return "[" + ",".join(format(value, ".17g") for value in embedding) + "]"
