"""FastAPI application factory and default ASGI app."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver

from incident_copilot.api.errors import register_exception_handlers
from incident_copilot.api.routes.health import router as health_router
from incident_copilot.api.routes.investigations import router as investigations_router
from incident_copilot.core.config import MetricsBackend, Settings, get_settings
from incident_copilot.core.logging import configure_logging
from incident_copilot.graph.bootstrap import (
    build_mixed_investigation_graph,
    build_offline_investigation_graph,
)
from incident_copilot.graph.builder import InvestigationGraph
from incident_copilot.investigations.checkpoint import open_checkpointer
from incident_copilot.investigations.repository import InMemoryInvestigationRepository
from incident_copilot.investigations.service import InvestigationService
from incident_copilot.tools.providers import PrometheusMetricsProvider


def _build_runtime_graph(
    settings: Settings,
    *,
    checkpointer: BaseCheckpointSaver[str],
) -> InvestigationGraph:
    """Select the explicitly configured metric adapter without probing it at startup."""
    if settings.metrics_backend is MetricsBackend.PROMETHEUS:
        return build_mixed_investigation_graph(
            metrics_provider=PrometheusMetricsProvider(
                settings.prometheus_base_url,
                timeout_seconds=settings.prometheus_timeout_seconds,
            ),
            checkpointer=checkpointer,
            require_human_review=True,
        )
    return build_offline_investigation_graph(
        checkpointer=checkpointer,
        require_human_review=True,
    )


def create_app(
    settings: Settings | None = None,
    investigation_service: InvestigationService | None = None,
) -> FastAPI:
    """Build an application instance with explicitly injected settings."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if investigation_service is not None:
            application.state.investigation_service = investigation_service
            yield
            return
        async with open_checkpointer(resolved_settings) as checkpointer:
            application.state.investigation_service = InvestigationService(
                graph=_build_runtime_graph(resolved_settings, checkpointer=checkpointer),
                repository=InMemoryInvestigationRepository(),
            )
            yield

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(investigations_router, prefix=resolved_settings.api_prefix)
    return app


app = create_app()
