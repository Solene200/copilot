"""Exercise the Phase 5 lifecycle against a running local HTTP server."""

import argparse
import json
import time
from typing import Any, cast
from urllib.request import Request, urlopen
from uuid import uuid4


def request_json(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one JSON request using only the standard library."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=10) as response:
        return cast(dict[str, Any], json.load(response))


def wait_for_status(base_url: str, investigation_id: str, expected: str) -> dict[str, Any]:
    """Poll the public status resource with a bounded local timeout."""
    for _ in range(100):
        result = request_json("GET", f"{base_url}/api/v1/investigations/{investigation_id}")
        if result["status"] == expected:
            return result
        time.sleep(0.05)
    raise TimeoutError(f"investigation did not reach {expected}")


def main() -> None:
    """Create, observe, approve, and print one fixture-backed report summary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    base_url = arguments.base_url.rstrip("/")
    created = request_json(
        "POST",
        f"{base_url}/api/v1/investigations",
        {
            "query": "payment-service error rate increased and requests timed out",
            "services": ["payment-service"],
            "start_time": "2026-07-18T10:20:00+08:00",
            "end_time": "2026-07-18T10:40:00+08:00",
            "symptoms": ["elevated error rate", "request timeouts"],
            "severity": "sev2",
            "environment": "production",
        },
        headers={"Idempotency-Key": f"demo-{uuid4().hex}"},
    )
    investigation_id = str(created["investigation_id"])
    paused = wait_for_status(base_url, investigation_id, "waiting_review")
    events_url = f"{base_url}/api/v1/investigations/{investigation_id}/events"
    with urlopen(events_url, timeout=10) as response:
        event_count = response.read().decode("utf-8").count("\nevent: ")
    request_json(
        "POST",
        f"{base_url}/api/v1/investigations/{investigation_id}/resume",
        {"action": "accept", "comment": "Reviewed in the local Phase 5 demo."},
    )
    completed = wait_for_status(base_url, investigation_id, "completed")
    report = cast(dict[str, Any], completed["report"])
    print(
        json.dumps(
            {
                "investigation_id": investigation_id,
                "thread_id": completed["thread_id"],
                "initial_run_id": created["run_id"],
                "resume_run_id": completed["run_id"],
                "paused_for": paused["review_request"],
                "streamed_event_count": event_count,
                "status": completed["status"],
                "report_id": report["report_id"],
                "disposition": report["disposition"],
                "supporting_evidence_count": len(report["supporting_evidence"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
