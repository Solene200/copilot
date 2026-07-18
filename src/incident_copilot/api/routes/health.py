"""存活检查接口。"""

from typing import cast

from fastapi import APIRouter, Request

from incident_copilot.api.schemas import HealthResponse
from incident_copilot.core.config import Settings

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """报告进程存活状态,但不要求可选外部服务可用。"""
    settings = cast(Settings, request.app.state.settings)
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment.value,
    )
