"""FastAPI application factory and default ASGI app.

中文教学说明:这是服务进程的“组合根”(composition root)。领域对象、Graph、工具与
Provider 都不会自行寻找依赖,而是在这里根据配置被显式装配,再通过 ``app.state`` 交给
HTTP 路由。阅读完整请求链时,可以从文件底部的 ``app = create_app()`` 向上追踪。
"""

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
    """Select the explicitly configured metric adapter without probing it at startup.

    中文:这里仅做“选择并注入 Adapter”,不在启动阶段探测远端服务。这样即使
    Prometheus 暂时不可用,应用也能启动,失败会在实际工具调用处被归一化和降级。
    """
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
    """Build an application instance with explicitly injected settings.

    中文:测试可以注入 Settings 或完整的 InvestigationService;生产路径则由 lifespan
    创建 Checkpointer、Graph、Repository 和 Service。该函数只负责依赖装配与 HTTP
    协议注册,不承担调查逻辑。
    """
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if investigation_service is not None:
            # 中文:注入路径让 API 测试复用受控 Service,同时仍统一执行关闭回收。
            application.state.investigation_service = investigation_service
            try:
                yield
            finally:
                await investigation_service.aclose()
            return
        # 中文:Checkpointer 必须覆盖整个应用生命周期;过早关闭会让 thread 无法恢复。
        async with open_checkpointer(resolved_settings) as checkpointer:
            service = InvestigationService(
                graph=_build_runtime_graph(resolved_settings, checkpointer=checkpointer),
                repository=InMemoryInvestigationRepository(),
            )
            application.state.investigation_service = service
            try:
                yield
            finally:
                # 中文:等待/取消进程内后台任务,避免应用退出后留下悬挂调查。
                await service.aclose()

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
