"""由版本化事故 Fixture 支持的确定性离线 Provider。"""

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.fixtures.schemas import IncidentFixture
from incident_copilot.tools.schemas import (
    GetRecentChangesInput,
    GetServiceTopologyInput,
    QueryContext,
    QueryMetricsInput,
    QueryTracesInput,
    SearchLogsInput,
    SearchRunbooksInput,
    SearchSimilarIncidentsInput,
)


class FixtureProvider:
    """基于一个不可变 Fixture 实现全部 Phase 2 Provider 端口。"""

    def __init__(self, fixture: IncidentFixture) -> None:
        self._fixture = fixture
        self._evidence = fixture.evidence

    @property
    def fixture(self) -> IncidentFixture:
        """仅向测试和演示元数据暴露已校验的外层对象。"""
        return self._fixture

    @classmethod
    def from_path(cls, path: Path) -> "FixtureProvider":
        """在处理任何查询前加载并完整校验 UTF-8 Fixture。"""
        fixture = IncidentFixture.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(fixture)

    @classmethod
    def payment_service(cls) -> "FixtureProvider":
        """加载仓库中的规范 payment-service 事故场景。"""
        repository_root = Path(__file__).parents[4]
        return cls.from_path(
            repository_root / "data" / "incidents" / "payment-service-pool-exhaustion.json"
        )

    async def search(self, query: SearchLogsInput, context: QueryContext) -> tuple[Evidence, ...]:
        """返回与服务、时间和文本匹配的确定性日志证据。"""
        del context
        # 过滤顺序与真实数据源一致: 先缩小来源/租户范围, 再按时间和全文条件筛选。
        matches = self._by_source_service(SourceType.LOG, query.service)
        matches = self._within_window(matches, query.start_time, query.end_time)
        if query.query is not None:
            matches = (item for item in matches if self._text_matches(item, query.query))
        return self._ordered(matches)[: query.limit]

    async def query(
        self,
        query: QueryMetricsInput | QueryTracesInput,
        context: QueryContext,
    ) -> tuple[Evidence, ...]:
        """按照已校验输入类型分发两个查询型 Provider 端口。"""
        del context
        if isinstance(query, QueryMetricsInput):
            # 指标名和聚合方式都必须精确匹配 Fixture metadata, 不做模糊猜测。
            matches = self._by_source_service(SourceType.METRIC, query.service)
            matches = self._within_window(matches, query.start_time, query.end_time)
            matches = (
                item
                for item in matches
                if item.metadata.get("metric_name") == query.metric_name
                and item.metadata.get("aggregation") == query.aggregation
            )
            return self._ordered(matches)[: query.limit]

        # 另一个联合类型分支只能是 Trace 查询; Pydantic 已在 Registry 入口完成校验。
        matches = self._by_source_service(SourceType.TRACE, query.service)
        matches = self._within_window(matches, query.start_time, query.end_time)
        if query.operation is not None:
            operation = query.operation.casefold()
            matches = (
                item
                for item in matches
                if str(item.metadata.get("operation", "")).casefold() == operation
            )
        if query.status is not None:
            matches = (item for item in matches if item.metadata.get("status") == query.status)
        return self._ordered(matches)[: query.limit]

    async def recent(
        self, query: GetRecentChangesInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        """优先返回最新的匹配部署或配置变更。"""
        del context
        # 变更记录按新到旧排序, 便于优先检查最接近故障开始时间的因果候选。
        matches = self._by_source_service(SourceType.CHANGE, query.service)
        matches = self._within_window(matches, query.start_time, query.end_time)
        if query.change_type is not None:
            matches = (
                item for item in matches if item.metadata.get("change_type") == query.change_type
            )
        return self._ordered(matches, newest_first=True)[: query.limit]

    async def get(
        self, query: GetServiceTopologyInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        """返回请求时间或更早时间中的最新拓扑快照。"""
        del context
        # 拓扑是时间点快照, 不能用故障发生之后才采集到的依赖关系解释过去。
        matches = self._by_source_service(SourceType.TOPOLOGY, query.service)
        matches = (
            item
            for item in matches
            if item.timestamp is not None
            and item.timestamp <= query.at_time
            and self._within_depth(item, query.depth)
        )
        return self._ordered(matches, newest_first=True)[: query.limit]

    async def search_runbooks(
        self, query: SearchRunbooksInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        """返回明确分类为 Runbook 的 Fixture 知识。"""
        del context
        matches = self._knowledge(query.service, "runbook", query.query)
        return self._ordered(matches, newest_first=True)[: query.limit]

    async def search_similar_incidents(
        self, query: SearchSimilarIncidentsInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        """返回调用方有界回溯窗口内的历史事故。"""
        del context
        earliest = query.before_time - timedelta(days=query.lookback_days)
        # 使用左闭右开区间排除当前事故本身, 防止“用答案检索答案”的数据泄漏。
        matches = self._knowledge(query.service, "incident", query.query)
        matches = (
            item
            for item in matches
            if item.timestamp is not None and earliest <= item.timestamp < query.before_time
        )
        return self._ordered(matches, newest_first=True)[: query.limit]

    def _knowledge(self, service: str, kind: str, query: str) -> Iterable[Evidence]:
        matches = self._by_source_service(SourceType.KNOWLEDGE, service)
        return (
            item
            for item in matches
            if item.metadata.get("knowledge_kind") == kind and self._text_matches(item, query)
        )

    def _by_source_service(self, source_type: SourceType, service: str) -> Iterable[Evidence]:
        return (
            item
            for item in self._evidence
            if item.source_type is source_type and item.service == service
        )

    @staticmethod
    def _within_window(
        evidence: Iterable[Evidence], start_time: datetime, end_time: datetime
    ) -> Iterable[Evidence]:
        return (item for item in evidence if FixtureProvider._overlaps(item, start_time, end_time))

    @staticmethod
    def _overlaps(item: Evidence, start_time: datetime, end_time: datetime) -> bool:
        # 单点证据按包含关系判断, 区间证据只要与查询窗口相交即可。
        if item.timestamp is not None:
            return start_time <= item.timestamp <= end_time
        if item.start_time is None or item.end_time is None:
            return False
        return item.start_time <= end_time and item.end_time >= start_time

    @staticmethod
    def _within_depth(item: Evidence, requested_depth: int) -> bool:
        value = item.metadata.get("depth")
        return isinstance(value, int) and not isinstance(value, bool) and value <= requested_depth

    @staticmethod
    def _text_matches(item: Evidence, query: str) -> bool:
        # 简单 AND 语义保持离线结果可解释: 每个规范化词都必须在证据文本中出现。
        terms = re.findall(r"[a-z0-9_.-]+", query.casefold())
        haystack = " ".join(
            (
                item.title,
                item.summary,
                json.dumps(item.content, sort_keys=True),
                json.dumps(item.metadata, sort_keys=True),
            )
        ).casefold()
        return bool(terms) and all(term in haystack for term in terms)

    @staticmethod
    def _ordered(
        evidence: Iterable[Evidence], *, newest_first: bool = False
    ) -> tuple[Evidence, ...]:
        minimum = datetime.min.replace(tzinfo=UTC)
        # evidence_id 作为同时间记录的稳定次级排序键, 保证跨平台运行结果一致。
        return tuple(
            sorted(
                evidence,
                key=lambda item: (item.timestamp or item.start_time or minimum, item.evidence_id),
                reverse=newest_first,
            )
        )
