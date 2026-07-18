"""Mixed-source graph contract with a controlled Prometheus HTTP boundary."""

from datetime import UTC, datetime

import pytest

from incident_copilot.domain.common import SourceType
from incident_copilot.graph.bootstrap import build_mixed_investigation_graph
from incident_copilot.graph.builder import create_initial_state
from incident_copilot.tools.providers import FixtureProvider, PrometheusMetricsProvider
from incident_copilot.tools.providers.prometheus import HttpResponse

TEST_NOW = datetime.now(UTC)


class PrometheusFixtureTransport:
    """Return the wire format emitted by a real query_range endpoint."""

    async def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        del url, timeout_seconds
        return HttpResponse(
            200,
            b"""{
                "status":"success",
                "data":{
                    "resultType":"matrix",
                    "result":[{
                        "metric":{"service":"payment-service"},
                        "values":[[1784341500,"0.98"],[1784341800,"0.99"]]
                    }]
                }
            }""",
        )


def fixed_clock() -> datetime:
    return TEST_NOW


@pytest.mark.asyncio
async def test_graph_uses_real_metrics_port_and_keeps_fixture_fallback_sources() -> None:
    fixture = FixtureProvider.payment_service()
    metrics = PrometheusMetricsProvider(
        "http://prometheus:9090",
        transport=PrometheusFixtureTransport(),
    )
    graph = build_mixed_investigation_graph(
        metrics_provider=metrics,
        fixture_provider=fixture,
        clock=fixed_clock,
    )

    state = await graph.ainvoke(create_initial_state(fixture.fixture.incident, clock=fixed_clock))

    metric_evidence = [item for item in state["evidence"] if item.source_type is SourceType.METRIC]
    assert len(metric_evidence) == 1
    assert metric_evidence[0].citation.uri.startswith("http://prometheus:9090/api/v1/query_range?")
    assert {item.source_type for item in state["evidence"]} >= {
        SourceType.METRIC,
        SourceType.LOG,
        SourceType.TRACE,
        SourceType.CHANGE,
    }
    assert any(
        citation.uri.startswith("http://prometheus:9090/")
        for citation in state["final_report"].citations
    )
