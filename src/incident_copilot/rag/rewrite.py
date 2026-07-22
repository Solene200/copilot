"""Fixture 检索使用的透明确定性查询改写。"""

import re
from typing import ClassVar


class QueryRewriter:
    """不调用 LLM,只做不改变调查意图的通用同义词归一化。"""

    # 中文短语出现时追加的已审核英文检索别名。
    _phrase_expansions: ClassVar[dict[str, str]] = {
        "连接池": "connection pool",
        "数据库": "database",
        "超时": "timeout",
        "域名解析": "dns resolution",
        "缓存": "cache",
        "历史故障": "historical incident",
    }
    # 仅追加等价写法,不追加具体服务、供应商或未经查询表达的根因词。
    _token_expansions: ClassVar[dict[str, tuple[str, ...]]] = {
        "db": ("database",),
        "database": ("db",),
        "postgresql": ("postgres",),
        "postgres": ("postgresql",),
        "timeout": ("latency", "timed", "out"),
        "timed-out": ("timeout",),
        "dns": ("resolution",),
        "caching": ("cache",),
    }

    def rewrite(self, query: str) -> str:
        """依次返回规范化原始词和去重后的已审核别名。"""
        # 先统一大小写和空白, 保留原始词序以便结果可解释。
        normalized = " ".join(query.strip().casefold().split())
        if len(normalized) < 2:
            raise ValueError("query must contain at least two characters")
        terms = re.findall(r"[a-z0-9_.:/-]+|[\u4e00-\u9fff]+", normalized)
        output: list[str] = []

        def append(value: str) -> None:
            # 用列表而不是 set 去重, 既避免重复又保持稳定输出顺序。
            if value and value not in output:
                output.append(value)

        for term in terms:
            append(term)
            for expansion in self._token_expansions.get(term, ()):
                append(expansion)
        for phrase, expansion in self._phrase_expansions.items():
            # 中文短语按包含关系追加英文别名, 不删除用户原始查询词。
            if phrase in normalized:
                for term in expansion.split():
                    append(term)
        return " ".join(output)
