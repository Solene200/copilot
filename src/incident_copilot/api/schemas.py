"""稳定的 API 响应 Schema。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ApiModel(BaseModel):
    """公开 API Schema 使用的严格基类。"""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    """不探测可选基础设施的存活响应。"""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ErrorDetail(ApiModel):
    """机器可读且安全的错误说明。"""

    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorResponse(ApiModel):
    """预期应用错误和校验错误使用的响应外层结构。"""

    error: ErrorDetail
    request_id: str
