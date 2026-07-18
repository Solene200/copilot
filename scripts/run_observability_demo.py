"""Verify OTLP-to-Prometheus ingestion and run a mixed-source investigation."""

import argparse
import asyncio
import json
from datetime import UTC, datetime

from incident_copilot.demo import shift_fixture_to_now, wait_for_metric
from incident_copilot.graph import build_mixed_investigation_graph, create_initial_state
from incident_copilot.tools.providers import FixtureProvider, PrometheusMetricsProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--wait-seconds", type=float, default=60)
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    if args.wait_seconds <= 0 or args.wait_seconds > 300:
        raise SystemExit("wait-seconds must be greater than zero and at most 300")
    provider = PrometheusMetricsProvider(args.prometheus_url, timeout_seconds=3)
    real_evidence = await wait_for_metric(provider, timeout_seconds=args.wait_seconds)
    reference_end = datetime.now(UTC)
    fixture = shift_fixture_to_now(
        FixtureProvider.payment_service().fixture,
        reference_end,
    )
    graph = build_mixed_investigation_graph(
        metrics_provider=provider,
        fixture_provider=FixtureProvider(fixture),
    )
    state = await graph.ainvoke(create_initial_state(fixture.incident))
    report = state["final_report"]
    prometheus_ids = tuple(
        item.evidence_id
        for item in state["evidence"]
        if item.citation.uri.startswith(args.prometheus_url.rstrip("/"))
    )
    if not prometheus_ids:
        raise RuntimeError("investigation completed without Prometheus evidence")
    print(
        json.dumps(
            {
                "status": "ok",
                "telemetry_path": (
                    "OTLP HTTP -> OpenTelemetry Collector -> Prometheus -> Provider -> LangGraph"
                ),
                "prometheus_probe_evidence_ids": [item.evidence_id for item in real_evidence],
                "graph_prometheus_evidence_ids": list(prometheus_ids),
                "stop_reason": state["stop_reason"],
                "tool_call_count": state["tool_call_count"],
                "report_id": report.report_id,
                "report_disposition": report.disposition,
                "citation_count": len(report.citations),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
