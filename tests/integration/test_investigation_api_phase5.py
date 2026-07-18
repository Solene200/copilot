"""HTTP and SSE acceptance tests for the Phase 5 investigation lifecycle."""

import json
import time
from collections.abc import Iterator
from typing import Any, cast

from fastapi.testclient import TestClient

from incident_copilot.core.config import RuntimeEnvironment, Settings
from incident_copilot.main import create_app
from incident_copilot.tools.providers.fixture import FixtureProvider


def request_payload(*, max_research_rounds: int = 2) -> dict[str, object]:
    incident = FixtureProvider.payment_service().fixture.incident
    return {
        "query": incident.raw_query,
        "services": list(incident.services),
        "start_time": incident.start_time.isoformat(),
        "end_time": incident.end_time.isoformat(),
        "symptoms": list(incident.symptoms),
        "severity": incident.severity.value,
        "environment": incident.environment.value,
        "options": {"max_research_rounds": max_research_rounds},
    }


def wait_for_status(
    client: TestClient,
    investigation_id: str,
    expected: str,
) -> dict[str, Any]:
    for _ in range(200):
        response = client.get(f"/api/v1/investigations/{investigation_id}")
        assert response.status_code == 200
        payload = cast(dict[str, Any], response.json())
        if payload["status"] == expected:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"investigation did not reach {expected}")


def parse_sse(text: str) -> Iterator[dict[str, Any]]:
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines or lines[0].startswith(":"):
            continue
        yield {
            "id": lines[0].removeprefix("id: "),
            "event": lines[1].removeprefix("event: "),
            "data": json.loads(lines[2].removeprefix("data: ")),
        }


def test_create_pause_stream_resume_and_duplicate_resume() -> None:
    app = create_app(Settings(environment=RuntimeEnvironment.TEST, _env_file=None))
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/investigations",
            json=request_payload(),
            headers={"Idempotency-Key": "api-create-pause"},
        )
        assert created.status_code == 202
        investigation_id = created.json()["investigation_id"]
        thread_id = created.json()["thread_id"]

        paused = wait_for_status(client, investigation_id, "waiting_review")
        assert paused["thread_id"] == thread_id
        assert paused["review_required"] is True
        assert paused["review_request"]["high_risk_actions"]

        streamed = client.get(f"/api/v1/investigations/{investigation_id}/events")
        events = list(parse_sse(streamed.text))
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert events[-1]["event"] == "review.required"
        assert [event["data"]["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert all(event["data"]["thread_id"] == thread_id for event in events)

        replay = client.get(
            f"/api/v1/investigations/{investigation_id}/events",
            headers={"Last-Event-ID": str(events[0]["id"])},
        )
        replayed_events = list(parse_sse(replay.text))
        assert replayed_events[0]["id"] == events[1]["id"]

        resumed = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={"action": "accept"},
        )
        duplicate = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={"action": "accept"},
        )
        assert resumed.status_code == 202
        assert resumed.json()["run_id"] != created.json()["run_id"]
        assert duplicate.status_code == 409

        completed = wait_for_status(client, investigation_id, "completed")
        assert completed["report"] is not None
        assert completed["review_required"] is False
        after_completion = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={"action": "accept"},
        )
        assert after_completion.status_code == 409


def test_invalid_feedback_missing_resource_and_invalid_event_cursor() -> None:
    app = create_app(Settings(environment=RuntimeEnvironment.TEST, _env_file=None))
    with TestClient(app) as client:
        assert client.get("/api/v1/investigations/inv_missing").status_code == 404
        assert (
            client.post(
                "/api/v1/investigations/inv_missing/resume",
                json={"action": "accept"},
            ).status_code
            == 404
        )
        created = client.post("/api/v1/investigations", json=request_payload())
        investigation_id = created.json()["investigation_id"]
        wait_for_status(client, investigation_id, "waiting_review")

        invalid_action = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={"action": "execute_remediation"},
        )
        missing_query = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={"action": "request_more_research"},
        )
        invalid_cursor = client.get(
            f"/api/v1/investigations/{investigation_id}/events",
            headers={"Last-Event-ID": "evt_another_1"},
        )

        assert invalid_action.status_code == 422
        assert missing_query.status_code == 422
        assert invalid_cursor.status_code == 400


def test_idempotent_create_and_exhausted_research_budget_conflicts() -> None:
    app = create_app(Settings(environment=RuntimeEnvironment.TEST, _env_file=None))
    with TestClient(app) as client:
        headers = {"Idempotency-Key": "same-api-request"}
        first = client.post(
            "/api/v1/investigations",
            json=request_payload(max_research_rounds=1),
            headers=headers,
        )
        second = client.post(
            "/api/v1/investigations",
            json=request_payload(max_research_rounds=1),
            headers=headers,
        )
        conflict = client.post(
            "/api/v1/investigations",
            json={**request_payload(max_research_rounds=2), "query": "different request"},
            headers=headers,
        )

        assert second.status_code == 202
        assert second.json()["replayed"] is True
        assert second.json()["investigation_id"] == first.json()["investigation_id"]
        assert conflict.status_code == 409

        investigation_id = first.json()["investigation_id"]
        wait_for_status(client, investigation_id, "waiting_review")
        no_budget = client.post(
            f"/api/v1/investigations/{investigation_id}/resume",
            json={
                "action": "request_more_research",
                "requested_queries": [
                    {
                        "query": "verify saturation",
                        "source_types": ["metric"],
                        "service": "payment-service",
                    }
                ],
            },
        )
        assert no_budget.status_code == 409


def test_stream_and_status_do_not_echo_sensitive_raw_input() -> None:
    app = create_app(Settings(environment=RuntimeEnvironment.TEST, _env_file=None))
    payload = request_payload()
    payload["query"] = "token=do-not-expose payment-service requests timed out"

    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=payload)
        investigation_id = created.json()["investigation_id"]
        paused = wait_for_status(client, investigation_id, "waiting_review")
        streamed = client.get(f"/api/v1/investigations/{investigation_id}/events")

    assert "do-not-expose" not in json.dumps(paused)
    assert "do-not-expose" not in streamed.text
