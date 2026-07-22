"""用于验证检索数据流的确定性本地 Embedding。"""

import hashlib
import math
from collections.abc import Sequence

from incident_copilot.rag.splitter import tokenize


class FakeEmbedding:
    """不依赖模型或网络的版本化有符号哈希 Embedding。

    该实现不会被描述为具有语义质量的 Embedding。Fixture 的大部分相关性信号由查询改写
    和词法检索承担。
    """

    model_name = "fake-signed-hash"
    version = "1"

    def __init__(self, *, dimension: int = 64) -> None:
        if dimension < 16 or dimension > 4_096:
            raise ValueError("fake embedding dimension must be between 16 and 4096")
        self.dimension = dimension

    def embed(self, text: str) -> tuple[float, ...]:
        """把规范化 Token 映射为稳定的单位长度有符号哈希向量。"""
        tokens = tokenize(text)
        if not tokens:
            raise ValueError("cannot embed text without supported tokens")
        values = [0.0] * self.dimension
        for token in tokens:
            # 哈希前半选择桶, 后一位选择正负号, 相同 Token 在任何进程中映射一致。
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            values[bucket] += sign
        # 单位化后可直接用点积计算余弦相似度, 无需在线 Embedding 服务。
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("fake embedding produced a zero vector")
        return tuple(value / norm for value in values)

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """对稳定序列生成 Embedding,且不产生批处理副作用。"""
        return tuple(self.embed(text) for text in texts)
