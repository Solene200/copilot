"""Offline Phase 4 composition over fixture observability and repository RAG."""

from collections.abc import Callable
from datetime import datetime

from langgraph.checkpoint.base import BaseCheckpointSaver

from incident_copilot.graph.builder import InvestigationGraph, build_investigation_graph
from incident_copilot.graph.model import FakeModelProvider, ModelProvider
from incident_copilot.graph.nodes import utc_now
from incident_copilot.rag.bootstrap import build_fixture_retriever
from incident_copilot.rag.provider import RagKnowledgeProvider
from incident_copilot.tools.builtin import ProviderBundle, build_tool_registry
from incident_copilot.tools.providers.fixture import FixtureProvider


def build_offline_investigation_graph(
    *,
    model: ModelProvider | None = None,
    clock: Callable[[], datetime] = utc_now,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    require_human_review: bool = False,
) -> InvestigationGraph:
    """Build a no-key/no-network investigation graph for tests and demos."""
    fixture = FixtureProvider.payment_service()
    retriever, _ = build_fixture_retriever(clock=clock)
    registry = build_tool_registry(
        ProviderBundle(
            logs=fixture,
            metrics=fixture,
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
