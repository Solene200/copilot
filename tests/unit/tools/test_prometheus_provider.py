"""Contract and failure tests for the Prometheus metrics adapter."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from incident_copilot.domain.common import SourceType
from incident_copilot.tools.exceptions import (
    ProviderInvalidQueryError,
    ProviderMalformedResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from incident_copilot.tools.providers.prometheus import (
    HttpResponse,
    PrometheusMetricsProvider,
)
from incident_copilot.tools.schemas import QueryContext, QueryMetricsInput


class FakeTransport:
    """Record adapter requests and return a controlled response or failure."""

    def __init__(
        self,
        response: HttpResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or HttpResponse(200, success_body())
        self.error = error
        self.calls: list[tuple[str, float]] = []

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        self.calls.append((url, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.response


def success_body() -> bytes:
    return b"""{
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{
                "metric": {
                    "__name__": "incident_demo_db_pool_utilization_ratio",
                    "service": "payment-service"
                },
                "values": [[1784341200, "0.96"], [1784341230, "0.99"]]
            }]
        }
    }"""


def query(
    *, metric_name: str = "db.pool.utilization", aggregation: str = "max"
) -> QueryMetricsInput:
    return QueryMetricsInput(
        service="payment-service",
        start_time=datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 18, 3, 0, tzinfo=UTC),
        metric_name=metric_name,
        aggregation=aggregation,
        limit=2,
    )


def context(*, expired: bool = False) -> QueryContext:
    deadline = datetime.now(UTC) + timedelta(seconds=-1 if expired else 5)
    return QueryContext(
        correlation_id="prometheus-provider-test",
        deadline=deadline,
        remaining_tool_calls=3,
    )


@pytest.mark.asyncio
async def test_success_preserves_source_time_service_and_citation() -> None:
    transport = FakeTransport()
    provider = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=transport,
        timeout_seconds=1.5,
    )

    evidence = await provider.query(query(), context())

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_type is SourceType.METRIC
    assert item.source_name == "prometheus-http"
    assert item.service == "payment-service"
    assert item.start_time == query().start_time
    assert item.end_time == query().end_time
    assert item.citation.uri == transport.calls[0][0]
    assert item.citation.content_hash == item.content_hash
    assert item.metadata["sample_count"] == 2
    parameters = parse_qs(urlsplit(transport.calls[0][0]).query)
    assert parameters["query"] == [
        'max by (service) (incident_demo_db_pool_utilization_ratio{service="payment-service"})'
    ]
    assert parameters["limit"] == ["2"]
    assert transport.calls[0][1] <= 1.5


@pytest.mark.asyncio
async def test_empty_result_is_not_fabricated() -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            b'{"status":"success","data":{"resultType":"matrix","result":[]}}',
        )
    )
    provider = PrometheusMetricsProvider("http://prometheus:9090", transport=transport)

    assert await provider.query(query(), context()) == ()


@pytest.mark.asyncio
async def test_unknown_metric_and_unsupported_aggregation_are_rejected_before_io() -> None:
    transport = FakeTransport()
    provider = PrometheusMetricsProvider("http://prometheus:9090", transport=transport)

    with pytest.raises(ProviderInvalidQueryError):
        await provider.query(query(metric_name="runtime.secret.metric"), context())
    with pytest.raises(ProviderInvalidQueryError):
        await provider.query(query(aggregation="rate"), context())

    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (400, ProviderInvalidQueryError),
        (429, ProviderRateLimitedError),
        (503, ProviderUnavailableError),
    ],
)
async def test_http_failures_are_normalized(status_code: int, error_type: type[Exception]) -> None:
    provider = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=FakeTransport(HttpResponse(status_code, b"{}")),
    )

    with pytest.raises(error_type):
        await provider.query(query(), context())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (TimeoutError(), ProviderTimeoutError),
        (OSError(), ProviderUnavailableError),
    ],
)
async def test_transport_failures_are_normalized(
    error: Exception, error_type: type[Exception]
) -> None:
    provider = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=FakeTransport(error=error),
    )

    with pytest.raises(error_type):
        await provider.query(query(), context())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"status":"success","data":{"resultType":"vector","result":[]}}',
        b'{"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[[1784341200,"NaN"]]}]}}',
        b'{"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[]}]}}',
    ],
)
async def test_malformed_responses_are_rejected(body: bytes) -> None:
    provider = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=FakeTransport(HttpResponse(200, body)),
    )

    with pytest.raises(ProviderMalformedResponseError):
        await provider.query(query(), context())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"success","data":{"resultType":"matrix","result":[{"metric":{"service":"other-service"},"values":[[1784341200,"0.9"]]}]}}',
        b'{"status":"success","data":{"resultType":"matrix","result":[{"metric":{"service":"payment-service"},"values":[[1784330000,"0.9"]]}]}}',
        b'{"status":"success","data":{"resultType":"matrix","result":[{"metric":{"service":"payment-service"},"values":[[1784341230,"0.9"],[1784341200,"0.8"]]}]}}',
    ],
)
async def test_untrusted_series_must_match_requested_service_and_time_window(
    body: bytes,
) -> None:
    provider = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=FakeTransport(HttpResponse(200, body)),
    )

    with pytest.raises(ProviderMalformedResponseError):
        await provider.query(query(), context())


@pytest.mark.asyncio
async def test_expired_context_does_not_call_transport() -> None:
    transport = FakeTransport()
    provider = PrometheusMetricsProvider("http://prometheus:9090", transport=transport)

    with pytest.raises(ProviderTimeoutError):
        await provider.query(query(), context(expired=True))

    assert transport.calls == []


@pytest.mark.parametrize(
    "base_url",
    ["prometheus:9090", "ftp://prometheus", "http://user:secret@prometheus:9090", "http://x/?q=1"],
)
def test_base_url_rejects_unsafe_or_ambiguous_values(base_url: str) -> None:
    with pytest.raises(ValueError):
        PrometheusMetricsProvider(base_url)
