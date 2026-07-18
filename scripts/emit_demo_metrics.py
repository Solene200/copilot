"""Emit a deterministic incident signal through OTLP for the local demo stack."""

import argparse
import os
import time

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0,
        help="Stop after this duration; zero keeps emitting until interrupted.",
    )
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    """Publish synthetic but explicitly labelled payment-service demo metrics."""
    args = parse_args()
    if args.duration_seconds < 0 or args.interval_seconds <= 0:
        raise SystemExit("duration must be non-negative and interval must be positive")
    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "http://127.0.0.1:14318/v1/metrics",
    )
    exporter = OTLPMetricExporter(endpoint=endpoint)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=1_000)
    provider = MeterProvider(
        resource=Resource.create(
            {
                "service.name": "incident-copilot-demo-emitter",
                "deployment.environment.name": "demo",
            }
        ),
        metric_readers=(reader,),
    )
    meter = provider.get_meter("incident-copilot.demo", "1.0")
    pool_utilization = meter.create_gauge(
        "incident_demo_db_pool_utilization_ratio",
        unit="1",
        description="Synthetic DB pool utilization used only by the portfolio demo.",
    )
    error_rate = meter.create_gauge(
        "incident_demo_http_server_error_rate_ratio",
        unit="1",
        description="Synthetic HTTP error ratio used only by the portfolio demo.",
    )
    attributes = {"service": "payment-service", "scenario": "pool-exhaustion"}
    started = time.monotonic()
    try:
        while args.duration_seconds == 0 or time.monotonic() - started < args.duration_seconds:
            pool_utilization.set(0.99, attributes)
            error_rate.set(0.31, attributes)
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            provider.force_flush(timeout_millis=5_000)
        finally:
            provider.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
