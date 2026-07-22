"""用于指标证据的有界 Prometheus HTTP API Adapter。"""

import asyncio
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPResponse
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Citation, Evidence, content_sha256
from incident_copilot.tools.exceptions import (
    ProviderInvalidQueryError,
    ProviderMalformedResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from incident_copilot.tools.schemas import QueryContext, QueryMetricsInput

# 写入错误、日志和 Evidence 的稳定 Provider 名称。
PROVIDER_NAME = "prometheus-http"
# 单次 Prometheus HTTP 响应允许读取的最大字节数。
MAX_RESPONSE_BYTES = 1_000_000
# 单条时间序列允许包含的最大采样点数。
MAX_POINTS_PER_SERIES = 240


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """让 Adapter 不依赖 HTTP SDK 的小型传输响应。"""

    # HTTP 响应状态码, 例如 200 或 429。
    status_code: int
    # 受到大小限制的原始响应体字节。
    body: bytes


class PrometheusTransport(Protocol):
    """真实 Adapter 和离线测试使用的可注入 HTTP 边界。"""

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """获取一个已经校验的 Prometheus API URL。"""
        ...


class ResponseTooLargeError(Exception):
    """远端响应体超过配置的证据上限时在内部抛出。"""


class UrllibPrometheusTransport:
    """具有严格响应大小限制的标准库 HTTP 传输实现。"""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """在事件循环之外执行阻塞 urllib I/O。"""
        return await asyncio.to_thread(self._get_sync, url, timeout_seconds)

    def _get_sync(self, url: str, timeout_seconds: float) -> HttpResponse:
        request = Request(
            url, headers={"Accept": "application/json", "User-Agent": "IncidentCopilot"}
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = self._read_bounded(response)
                return HttpResponse(status_code=response.status, body=body)
        except HTTPError as exc:
            body = self._read_bounded(exc)
            return HttpResponse(status_code=exc.code, body=body)

    def _read_bounded(self, response: HTTPResponse | HTTPError) -> bytes:
        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise ResponseTooLargeError("Prometheus response exceeded the configured byte limit")
        return body


class _PrometheusSeries(BaseModel):
    # 忽略 Prometheus 额外字段, 并让校验后的响应对象不可修改。
    model_config = ConfigDict(extra="ignore", frozen=True)

    # Prometheus 返回的标签字典, 必须包含请求服务。
    metric: dict[str, str]
    # 按时间递增的时间戳与字符串数值序列。
    values: tuple[tuple[float, str], ...]


class _PrometheusData(BaseModel):
    # 忽略 Prometheus 额外字段, 并让校验后的响应对象不可修改。
    model_config = ConfigDict(extra="ignore", frozen=True)

    # 范围查询只接受 matrix 时间序列响应。
    result_type: Literal["matrix"] = Field(alias="resultType")
    # Prometheus 返回的全部时间序列。
    result: tuple[_PrometheusSeries, ...]


class _PrometheusEnvelope(BaseModel):
    # 忽略 Prometheus 额外字段, 并让校验后的响应对象不可修改。
    model_config = ConfigDict(extra="ignore", frozen=True)

    # Provider 只接受明确成功的 Prometheus 响应。
    status: Literal["success"]
    # 成功响应中的 matrix 数据对象。
    data: _PrometheusData


@dataclass(frozen=True, slots=True)
class _MetricMapping:
    # 白名单领域指标对应的真实 Prometheus 指标名。
    prometheus_name: str
    # 指标数值的展示单位, 例如 ratio。
    unit: str
    # 该指标允许调用方选择的聚合方式。
    supported_aggregations: frozenset[str]


# 领域指标名到安全 Prometheus 查询信息的固定白名单。
METRIC_MAPPINGS: Mapping[str, _MetricMapping] = {
    "db.pool.utilization": _MetricMapping(
        prometheus_name="incident_demo_db_pool_utilization_ratio",
        unit="ratio",
        supported_aggregations=frozenset({"avg", "max", "min", "p95"}),
    ),
    "http.server.error_rate": _MetricMapping(
        prometheus_name="incident_demo_http_server_error_rate_ratio",
        unit="ratio",
        supported_aggregations=frozenset({"avg", "max", "rate"}),
    ),
}


class PrometheusMetricsProvider:
    """把白名单领域指标转换为可引用的 Prometheus 证据。"""

    def __init__(
        self,
        base_url: str,
        *,
        transport: PrometheusTransport | None = None,
        timeout_seconds: float = 2.0,
        clock: Clock = utc_now,
    ) -> None:
        self._base_url = self._validate_base_url(base_url)
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be greater than 0 and at most 30")
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibPrometheusTransport()
        self._clock = clock

    async def query(self, query: QueryMetricsInput, context: QueryContext) -> tuple[Evidence, ...]:
        """执行一次安全范围查询并保留来源定位信息。"""
        # 外部输入只能选择领域指标白名单, 不能把任意 PromQL 直接发送给 Prometheus。
        mapping = METRIC_MAPPINGS.get(query.metric_name)
        if mapping is None or query.aggregation not in mapping.supported_aggregations:
            raise ProviderInvalidQueryError(
                "metric or aggregation is not available through this adapter",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )

        # PromQL 完全由白名单映射和已经校验的服务名生成, 查询文本不参与模板拼接。
        promql = self._build_promql(mapping, query)
        request_url = self._build_request_url(promql, query)
        remaining_seconds = (context.deadline - self._clock()).total_seconds()
        timeout_seconds = min(self._timeout_seconds, remaining_seconds)
        if timeout_seconds <= 0:
            raise ProviderTimeoutError(
                "query deadline exceeded before the provider call",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        try:
            # Transport 是可替换边界: 生产使用 urllib, 测试使用不会联网的受控 Fake。
            response = await self._transport.get(request_url, timeout_seconds=timeout_seconds)
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "Prometheus query timed out",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            ) from exc
        except ResponseTooLargeError as exc:
            raise ProviderMalformedResponseError(
                "Prometheus response exceeded the configured size limit",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            ) from exc
        except (OSError, URLError) as exc:
            raise ProviderUnavailableError(
                "Prometheus is unavailable",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            ) from exc

        # HTTP 状态和 JSON 结构分别归一化, 调用方只处理稳定 ProviderError 分类。
        self._raise_for_status(response.status_code)
        envelope = self._parse_response(response.body)
        if len(envelope.data.result) > query.limit:
            raise ProviderMalformedResponseError(
                "Prometheus returned more series than requested",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        return tuple(
            self._series_to_evidence(
                series,
                query=query,
                mapping=mapping,
                request_url=request_url,
                series_index=index,
            )
            for index, series in enumerate(envelope.data.result)
        )

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        # 基础地址只允许无凭据、无查询参数的 HTTP(S) 主机, 防止秘密写入 Citation URL。
        normalized = base_url.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("Prometheus base_url must be an absolute http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Prometheus base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Prometheus base_url must not contain a query or fragment")
        return normalized

    @staticmethod
    def _build_promql(mapping: _MetricMapping, query: QueryMetricsInput) -> str:
        selector = f'{mapping.prometheus_name}{{service="{query.service}"}}'
        aggregation = query.aggregation
        # rate/p95 映射到受控模板, 其他聚合也已在 mapping 白名单中验证。
        if aggregation == "rate":
            return f"max by (service) ({selector})"
        if aggregation == "p95":
            return f"quantile by (service) (0.95, {selector})"
        return f"{aggregation} by (service) ({selector})"

    def _build_request_url(self, promql: str, query: QueryMetricsInput) -> str:
        duration_seconds = (query.end_time - query.start_time).total_seconds()
        # 动态 step 保证最长查询窗口也不会超过单序列采样点上限。
        step_seconds = max(15, math.ceil(duration_seconds / MAX_POINTS_PER_SERIES))
        parameters = urlencode(
            {
                "query": promql,
                "start": query.start_time.timestamp(),
                "end": query.end_time.timestamp(),
                "step": step_seconds,
                "limit": query.limit,
            }
        )
        return f"{self._base_url}/api/v1/query_range?{parameters}"

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        # HTTP 状态映射为稳定错误类别, Registry 据此决定是否允许重试。
        if status_code in {400, 422}:
            raise ProviderInvalidQueryError(
                "Prometheus rejected the generated query",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        if status_code == 429:
            raise ProviderRateLimitedError(
                "Prometheus rate limited the query",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Prometheus returned an unavailable response",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )

    @staticmethod
    def _parse_response(body: bytes) -> _PrometheusEnvelope:
        try:
            return _PrometheusEnvelope.model_validate_json(body)
        except ValidationError as exc:
            raise ProviderMalformedResponseError(
                "Prometheus returned a response outside the expected matrix schema",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            ) from exc

    def _series_to_evidence(
        self,
        series: _PrometheusSeries,
        *,
        query: QueryMetricsInput,
        mapping: _MetricMapping,
        request_url: str,
        series_index: int,
    ) -> Evidence:
        # 先执行资源与租户边界校验, 再解析采样点, 防止大响应占用过多内存。
        if len(series.values) > MAX_POINTS_PER_SERIES:
            raise ProviderMalformedResponseError(
                "Prometheus series exceeded the configured point limit",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        if series.metric.get("service") != query.service:
            raise ProviderMalformedResponseError(
                "Prometheus returned a series outside the requested service",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        points: list[JsonValue] = []
        numeric_values: list[float] = []
        previous_timestamp: float | None = None
        for timestamp, raw_value in series.values:
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ProviderMalformedResponseError(
                    "Prometheus returned a non-numeric sample",
                    provider_name=PROVIDER_NAME,
                    operation="query_metrics",
                ) from exc
            if not math.isfinite(timestamp) or not math.isfinite(value):
                raise ProviderMalformedResponseError(
                    "Prometheus returned a non-finite sample",
                    provider_name=PROVIDER_NAME,
                    operation="query_metrics",
                )
            if not query.start_time.timestamp() <= timestamp <= query.end_time.timestamp():
                raise ProviderMalformedResponseError(
                    "Prometheus returned a sample outside the requested time range",
                    provider_name=PROVIDER_NAME,
                    operation="query_metrics",
                )
            # 严格递增能避免重复点或乱序点改变 maximum/latest 的业务含义。
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ProviderMalformedResponseError(
                    "Prometheus returned samples outside strict timestamp order",
                    provider_name=PROVIDER_NAME,
                    operation="query_metrics",
                )
            previous_timestamp = timestamp
            points.append(
                {
                    "timestamp": datetime.fromtimestamp(timestamp, tz=UTC).isoformat(),
                    "value": value,
                }
            )
            numeric_values.append(value)
        if not numeric_values:
            raise ProviderMalformedResponseError(
                "Prometheus returned an empty series",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )

        content: JsonValue = {
            "metric_name": query.metric_name,
            "prometheus_metric": mapping.prometheus_name,
            "aggregation": query.aggregation,
            "unit": mapping.unit,
            "labels": dict(sorted(series.metric.items())),
            "points": points,
        }
        # 内容哈希把 Citation 与 Evidence 载荷绑定, 报告阶段可以验证引用未被替换。
        content_hash = content_sha256(content)
        # Evidence ID 由查询范围、序列序号和内容生成, 相同响应重放仍得到相同标识。
        identity = hashlib.sha256(
            (
                f"{query.service}|{query.metric_name}|{query.aggregation}|"
                f"{query.start_time.isoformat()}|{query.end_time.isoformat()}|{series_index}|"
                f"{content_hash}"
            ).encode()
        ).hexdigest()[:32]
        maximum = max(numeric_values)
        latest = numeric_values[-1]
        collected_at = self._clock()
        citation = Citation.for_content(
            content=content,
            citation_id=f"cit_prom_{identity}",
            uri=request_url,
            locator=f"matrix result[{series_index}] ({len(points)} samples)",
            display_name=f"Prometheus: {query.metric_name}",
            retrieved_at=collected_at,
        )
        return Evidence(
            evidence_id=f"ev_prom_{identity}",
            source_type=SourceType.METRIC,
            source_name=PROVIDER_NAME,
            title=f"{query.service} {query.metric_name}",
            content=content,
            summary=(
                f"Prometheus observed {query.metric_name} for {query.service}: "
                f"maximum {maximum:.4g} {mapping.unit}, latest {latest:.4g} {mapping.unit}."
            ),
            start_time=query.start_time,
            end_time=query.end_time,
            service=query.service,
            relevance_score=0.9,
            reliability_score=0.9,
            metadata={
                "metric_name": query.metric_name,
                "aggregation": query.aggregation,
                "prometheus_metric": mapping.prometheus_name,
                "sample_count": len(points),
            },
            citation=citation,
            collected_at=collected_at,
        )
