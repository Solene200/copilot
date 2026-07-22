"""七个 Phase 2 只读工具的组合。"""

from collections.abc import Sequence
from dataclasses import dataclass

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.tools.interfaces import (
    ChangeProvider,
    KnowledgeProvider,
    LogProvider,
    MetricsProvider,
    TopologyProvider,
    TraceProvider,
)
from incident_copilot.tools.registry import ToolDefinition, ToolRegistry
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


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """为每个 Phase 2 Provider 端口提供显式依赖注入。"""

    # 提供日志 Evidence 的 Adapter。
    logs: LogProvider
    # 提供指标 Evidence 的 Adapter。
    metrics: MetricsProvider
    # 提供分布式 Trace Evidence 的 Adapter。
    traces: TraceProvider
    # 提供部署和配置变更 Evidence 的 Adapter。
    changes: ChangeProvider
    # 提供服务依赖拓扑 Evidence 的 Adapter。
    topology: TopologyProvider
    # 提供 Runbook 和历史故障 Evidence 的 Adapter。
    knowledge: KnowledgeProvider


def build_tool_registry(
    providers: ProviderBundle,
    *,
    timeout_seconds: float = 2.0,
    max_retries: int = 1,
    retry_backoff_seconds: float = 0.01,
    clock: Clock = utc_now,
) -> ToolRegistry:
    """使用注入的 Provider 注册全部七个工具。"""
    # Registry 统一承载安全策略; 下方闭包只负责把强类型参数转发到对应端口。
    registry = ToolRegistry(retry_backoff_seconds=retry_backoff_seconds, clock=clock)

    async def search_logs(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        return await providers.logs.search(query, context)

    async def query_metrics(query: QueryMetricsInput, context: QueryContext) -> Sequence[Evidence]:
        return await providers.metrics.query(query, context)

    async def query_traces(query: QueryTracesInput, context: QueryContext) -> Sequence[Evidence]:
        return await providers.traces.query(query, context)

    async def get_service_topology(
        query: GetServiceTopologyInput, context: QueryContext
    ) -> Sequence[Evidence]:
        return await providers.topology.get(query, context)

    async def get_recent_changes(
        query: GetRecentChangesInput, context: QueryContext
    ) -> Sequence[Evidence]:
        return await providers.changes.recent(query, context)

    async def search_runbooks(
        query: SearchRunbooksInput, context: QueryContext
    ) -> Sequence[Evidence]:
        return await providers.knowledge.search_runbooks(query, context)

    async def search_similar_incidents(
        query: SearchSimilarIncidentsInput, context: QueryContext
    ) -> Sequence[Evidence]:
        return await providers.knowledge.search_similar_incidents(query, context)

    # 每项注册同时绑定输入 Schema 和允许的 Evidence 来源, 返回结果会据此二次校验。
    registry.register(
        ToolDefinition(
            "search_logs",
            SearchLogsInput,
            search_logs,
            frozenset({SourceType.LOG}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "query_metrics",
            QueryMetricsInput,
            query_metrics,
            frozenset({SourceType.METRIC}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "query_traces",
            QueryTracesInput,
            query_traces,
            frozenset({SourceType.TRACE}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "get_service_topology",
            GetServiceTopologyInput,
            get_service_topology,
            frozenset({SourceType.TOPOLOGY}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "get_recent_changes",
            GetRecentChangesInput,
            get_recent_changes,
            frozenset({SourceType.CHANGE}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "search_runbooks",
            SearchRunbooksInput,
            search_runbooks,
            frozenset({SourceType.KNOWLEDGE}),
            timeout_seconds,
            max_retries,
        )
    )
    registry.register(
        ToolDefinition(
            "search_similar_incidents",
            SearchSimilarIncidentsInput,
            search_similar_incidents,
            frozenset({SourceType.KNOWLEDGE}),
            timeout_seconds,
            max_retries,
        )
    )
    return registry
