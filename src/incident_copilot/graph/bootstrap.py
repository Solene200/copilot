"""纯 Fixture 和混合 Provider 调查 Graph 的组合根。"""

from collections.abc import Callable
from datetime import datetime

from langgraph.checkpoint.base import BaseCheckpointSaver

from incident_copilot.graph.builder import InvestigationGraph, build_investigation_graph
from incident_copilot.graph.model import FakeModelProvider, ModelProvider
from incident_copilot.graph.nodes import utc_now
from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.provider import RagKnowledgeProvider
from incident_copilot.tools.builtin import ProviderBundle, build_tool_registry
from incident_copilot.tools.interfaces import MetricsProvider
from incident_copilot.tools.providers.fixture import FixtureProvider


def build_offline_investigation_graph(
    *,
    model: ModelProvider | None = None,
    fixture_provider: FixtureProvider | None = None,
    clock: Callable[[], datetime] = utc_now,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    require_human_review: bool = False,
) -> InvestigationGraph:
    """为测试和演示构造无需密钥与网络的调查 Graph。"""
    fixture = fixture_provider or FixtureProvider.payment_service()
    return build_mixed_investigation_graph(
        metrics_provider=fixture,
        model=model,
        fixture_provider=fixture,
        clock=clock,
        checkpointer=checkpointer,
        require_human_review=require_human_review,
    )


def build_mixed_investigation_graph(
    *,
    metrics_provider: MetricsProvider,
    model: ModelProvider | None = None,
    fixture_provider: FixtureProvider | None = None,
    clock: Callable[[], datetime] = utc_now,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    require_human_review: bool = False,
) -> InvestigationGraph:
    """构造使用真实指标和确定性降级数据源的 Graph。"""
    fixture = fixture_provider or FixtureProvider.payment_service()
    retriever, _ = build_fixture_retriever(clock=clock)
    registry = build_tool_registry(
        ProviderBundle(
            logs=fixture,
            metrics=metrics_provider,
            traces=fixture,
            changes=fixture,
            topology=fixture,
            knowledge=RagKnowledgeProvider(retriever),
        ),
        retry_backoff_seconds=0,
    )
    return build_investigation_graph(
        registry=registry,
        model=model or FakeModelProvider(),
        clock=clock,
        checkpointer=checkpointer,
        require_human_review=require_human_review,
    )
