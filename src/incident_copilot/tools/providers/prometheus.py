"""Bounded Prometheus HTTP API adapter for metric evidence."""

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPResponse
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Citation, Evidence
from incident_copilot.tools.exceptions import (
    ProviderInvalidQueryError,
    ProviderMalformedResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from incident_copilot.tools.schemas import QueryContext, QueryMetricsInput

PROVIDER_NAME = "prometheus-http"
MAX_RESPONSE_BYTES = 1_000_000
MAX_POINTS_PER_SERIES = 240


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Small transport response that keeps the adapter independent of an HTTP SDK."""

    status_code: int
    body: bytes


class PrometheusTransport(Protocol):
    """Injectable HTTP boundary used by the real adapter and offline tests."""

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """Fetch one already validated Prometheus API URL."""
        ...


class ResponseTooLargeError(Exception):
    """Raised internally when a remote body exceeds the configured evidence bound."""


class UrllibPrometheusTransport:
    """Standard-library HTTP transport with a strict response-size limit."""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        """Run blocking urllib I/O outside the event loop."""
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
    model_config = ConfigDict(extra="ignore", frozen=True)

    metric: dict[str, str]
    values: tuple[tuple[float, str], ...]


class _PrometheusData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    result_type: Literal["matrix"] = Field(alias="resultType")
    result: tuple[_PrometheusSeries, ...]


class _PrometheusEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["success"]
    data: _PrometheusData


@dataclass(frozen=True, slots=True)
class _MetricMapping:
    prometheus_name: str
    unit: str
    supported_aggregations: frozenset[str]


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
    """Translate allow-listed domain metrics into citable Prometheus evidence."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: PrometheusTransport | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._base_url = self._validate_base_url(base_url)
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be greater than 0 and at most 30")
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibPrometheusTransport()

    async def query(self, query: QueryMetricsInput, context: QueryContext) -> tuple[Evidence, ...]:
        """Execute one safe range query and preserve its source locator."""
        mapping = METRIC_MAPPINGS.get(query.metric_name)
        if mapping is None or query.aggregation not in mapping.supported_aggregations:
            raise ProviderInvalidQueryError(
                "metric or aggregation is not available through this adapter",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )

        promql = self._build_promql(mapping, query)
        request_url = self._build_request_url(promql, query)
        remaining_seconds = (context.deadline - datetime.now(UTC)).total_seconds()
        timeout_seconds = min(self._timeout_seconds, remaining_seconds)
        if timeout_seconds <= 0:
            raise ProviderTimeoutError(
                "query deadline exceeded before the provider call",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        try:
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
        if aggregation == "rate":
            return f"max by (service) ({selector})"
        if aggregation == "p95":
            return f"quantile by (service) (0.95, {selector})"
        return f"{aggregation} by (service) ({selector})"

    def _build_request_url(self, promql: str, query: QueryMetricsInput) -> str:
        duration_seconds = (query.end_time - query.start_time).total_seconds()
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

    @staticmethod
    def _series_to_evidence(
        series: _PrometheusSeries,
        *,
        query: QueryMetricsInput,
        mapping: _MetricMapping,
        request_url: str,
        series_index: int,
    ) -> Evidence:
        if len(series.values) > MAX_POINTS_PER_SERIES:
            raise ProviderMalformedResponseError(
                "Prometheus series exceeded the configured point limit",
                provider_name=PROVIDER_NAME,
                operation="query_metrics",
            )
        points: list[dict[str, float | str]] = []
        numeric_values: list[float] = []
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

        content = {
            "metric_name": query.metric_name,
            "prometheus_metric": mapping.prometheus_name,
            "aggregation": query.aggregation,
            "unit": mapping.unit,
            "labels": dict(sorted(series.metric.items())),
            "points": points,
        }
        canonical_content = json.dumps(
            content, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
        content_hash = hashlib.sha256(canonical_content).hexdigest()
        identity = hashlib.sha256(
            (
                f"{query.service}|{query.metric_name}|{query.aggregation}|"
                f"{query.start_time.isoformat()}|{query.end_time.isoformat()}|{series_index}|"
                f"{content_hash}"
            ).encode()
        ).hexdigest()[:32]
        maximum = max(numeric_values)
        latest = numeric_values[-1]
        collected_at = datetime.now(UTC)
        citation = Citation(
            citation_id=f"cit_prom_{identity}",
            uri=request_url,
            locator=f"matrix result[{series_index}] ({len(points)} samples)",
            display_name=f"Prometheus: {query.metric_name}",
            retrieved_at=collected_at,
            content_hash=content_hash,
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
            content_hash=content_hash,
            collected_at=collected_at,
        )
