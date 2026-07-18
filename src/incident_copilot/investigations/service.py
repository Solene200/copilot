"""Checkpoint-aware investigation lifecycle and safe event projection."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import JsonValue

from incident_copilot.core.exceptions import ResourceConflictError
from incident_copilot.core.logging import redact_value
from incident_copilot.domain.evidence import EvidenceRef
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.review import HumanFeedback, HumanReviewRequest, ReviewAction
from incident_copilot.graph.builder import InvestigationGraph, create_initial_state
from incident_copilot.graph.schemas import InvestigationOptions, ModelUsage, StepResult, StepStatus
from incident_copilot.graph.state import InvestigationState
from incident_copilot.investigations.models import (
    EventType,
    InvestigationEvent,
    InvestigationRecord,
    InvestigationStatus,
)
from incident_copilot.investigations.repository import InvestigationRepository

logger = logging.getLogger(__name__)
_QUIESCENT_STATUSES = {
    InvestigationStatus.WAITING_REVIEW,
    InvestigationStatus.COMPLETED,
    InvestigationStatus.FAILED,
}


class InvestigationService:
    """Own task transitions while LangGraph owns checkpointed workflow state."""

    def __init__(
        self,
        *,
        graph: InvestigationGraph,
        repository: InvestigationRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._graph = graph
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def repository(self) -> InvestigationRepository:
        """Expose the repository port to the SSE transport adapter."""
        return self._repository

    async def create(
        self,
        *,
        incident: IncidentContext,
        options: InvestigationOptions,
        request_fingerprint: str,
        idempotency_key: str | None,
    ) -> tuple[InvestigationRecord, bool]:
        """Create idempotently and schedule one offline background execution."""
        now = self._clock()
        investigation_id = f"inv_{uuid4().hex}"
        record = InvestigationRecord(
            investigation_id=investigation_id,
            incident_id=incident.incident_id,
            thread_id=f"thread_{uuid4().hex}",
            run_id=f"run_{uuid4().hex}",
            incident=incident,
            options=options,
            request_fingerprint=request_fingerprint,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        stored, created = await self._repository.create(record)
        if not created:
            return stored, False
        await self._append_event(stored, EventType.INVESTIGATION_QUEUED, {"status": "pending"})
        self._start_task(stored.investigation_id, self._run_initial(stored.investigation_id))
        return stored, True

    async def get(self, investigation_id: str) -> InvestigationRecord:
        """Return current task metadata without loading raw graph state."""
        return await self._repository.get(investigation_id)

    async def resume(
        self,
        investigation_id: str,
        feedback: HumanFeedback,
    ) -> InvestigationRecord:
        """Atomically claim one paused checkpoint and schedule its resume command."""
        lock = self._locks.setdefault(investigation_id, asyncio.Lock())
        async with lock:
            record = await self._repository.get(investigation_id)
            if record.status is not InvestigationStatus.WAITING_REVIEW:
                raise ResourceConflictError(
                    "Investigation is not waiting for review",
                    details={"status": record.status.value},
                )
            config = self._config(record.thread_id)
            snapshot = await self._graph.aget_state(config)
            state = cast(InvestigationState, snapshot.values)
            if feedback.action is ReviewAction.REQUEST_MORE_RESEARCH:
                self._ensure_research_budget(state)
            now = self._clock()
            claimed = record.model_copy(
                update={
                    "run_id": f"run_{uuid4().hex}",
                    "status": InvestigationStatus.RUNNING,
                    "review_request": None,
                    "updated_at": now,
                    "version": record.version + 1,
                }
            )
            claimed = await self._repository.update(claimed, expected_version=record.version)
            await self._append_event(
                claimed,
                EventType.INVESTIGATION_STARTED,
                {"mode": "resume", "review_action": feedback.action.value},
            )
            update: dict[str, object] | None = None
            if feedback.action is ReviewAction.REQUEST_MORE_RESEARCH:
                update = {
                    "deadline_at": now + timedelta(seconds=record.options.timeout_seconds),
                    "deadline_exceeded": False,
                }
            command: Command[Any] = Command(
                resume=feedback.model_dump(mode="json"),
                update=update,
            )
            self._start_task(
                investigation_id,
                self._execute(investigation_id, command),
            )
            return claimed

    async def wait_until_quiescent(
        self,
        investigation_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> InvestigationRecord:
        """Wait for a pause or terminal state; intended for controlled clients and tests."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            record = await self._repository.get(investigation_id)
            if record.status in _QUIESCENT_STATUSES:
                return record
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("investigation did not reach a quiescent state")
            await asyncio.sleep(0.01)

    def _start_task(self, investigation_id: str, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine, name=f"investigation:{investigation_id}")
        self._tasks[investigation_id] = task

        def remove(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(investigation_id) is completed:
                self._tasks.pop(investigation_id, None)

        task.add_done_callback(remove)

    async def _run_initial(self, investigation_id: str) -> None:
        record = await self._repository.get(investigation_id)
        running = record.model_copy(
            update={
                "status": InvestigationStatus.RUNNING,
                "updated_at": self._clock(),
                "version": record.version + 1,
            }
        )
        running = await self._repository.update(running, expected_version=record.version)
        await self._append_event(
            running,
            EventType.INVESTIGATION_STARTED,
            {"mode": "initial"},
        )
        initial = create_initial_state(running.incident, options=running.options, clock=self._clock)
        await self._execute(investigation_id, initial)

    async def _execute(
        self,
        investigation_id: str,
        graph_input: InvestigationState | Command[Any],
    ) -> None:
        record = await self._repository.get(investigation_id)
        config = self._config(record.thread_id)
        try:
            async for update in self._graph.astream(
                graph_input,
                config,
                stream_mode="updates",
            ):
                if isinstance(update, Mapping):
                    await self._project_graph_update(
                        record,
                        cast(Mapping[object, object], update),
                    )
            snapshot = await self._graph.aget_state(config)
            latest = await self._repository.get(investigation_id)
            interrupt_value = self._interrupt_value(snapshot.tasks)
            values = cast(InvestigationState, snapshot.values)
            report = values.get("final_report")
            if interrupt_value is not None:
                review_request = HumanReviewRequest.model_validate(interrupt_value)
                waiting = latest.model_copy(
                    update={
                        "status": InvestigationStatus.WAITING_REVIEW,
                        "report": report,
                        "review_request": review_request,
                        "updated_at": self._clock(),
                        "version": latest.version + 1,
                    }
                )
                waiting = await self._repository.update(waiting, expected_version=latest.version)
                await self._append_event(
                    waiting,
                    EventType.REVIEW_REQUIRED,
                    cast(dict[str, JsonValue], review_request.model_dump(mode="json")),
                )
                return
            completed = latest.model_copy(
                update={
                    "status": InvestigationStatus.COMPLETED,
                    "report": report,
                    "review_request": None,
                    "updated_at": self._clock(),
                    "version": latest.version + 1,
                }
            )
            completed = await self._repository.update(completed, expected_version=latest.version)
            await self._append_event(
                completed,
                EventType.REPORT_COMPLETED,
                {"report_id": report.report_id if report is not None else None},
            )
        except Exception:
            logger.exception(
                "Investigation execution failed",
                extra={"investigation_id": investigation_id, "thread_id": record.thread_id},
            )
            latest = await self._repository.get(investigation_id)
            failed = latest.model_copy(
                update={
                    "status": InvestigationStatus.FAILED,
                    "error_message": "Investigation execution failed",
                    "updated_at": self._clock(),
                    "version": latest.version + 1,
                }
            )
            failed = await self._repository.update(failed, expected_version=latest.version)
            await self._append_event(
                failed,
                EventType.INVESTIGATION_FAILED,
                {"message": "Investigation execution failed"},
            )

    async def _project_graph_update(
        self,
        record: InvestigationRecord,
        update: Mapping[object, object],
    ) -> None:
        for raw_node, raw_value in update.items():
            node = str(raw_node)
            if node == "__interrupt__":
                continue
            await self._append_event(record, EventType.NODE_COMPLETED, {"node": node})
            if not isinstance(raw_value, Mapping):
                continue
            node_update = cast(Mapping[str, object], raw_value)
            for step in self._models(node_update.get("completed_steps"), StepResult):
                event_type = (
                    EventType.TOOL_COMPLETED
                    if step.status is StepStatus.COMPLETED
                    else EventType.TOOL_FAILED
                )
                await self._append_event(
                    record,
                    event_type,
                    {
                        "step_id": step.step_id,
                        "tool_name": step.tool_name,
                        "status": step.status.value,
                        "evidence_ids": list(step.evidence_ids),
                    },
                )
            for evidence in self._models(node_update.get("evidence"), EvidenceRef):
                timestamp = evidence.timestamp or evidence.start_time
                await self._append_event(
                    record,
                    EventType.EVIDENCE_ADDED,
                    {
                        "evidence_id": evidence.evidence_id,
                        "source_type": evidence.source_type.value,
                        "service": evidence.service,
                        "timestamp": timestamp.isoformat() if timestamp is not None else None,
                        "summary": evidence.summary,
                        "citation": evidence.citation.model_dump(mode="json"),
                    },
                )
            if "hypotheses" in node_update:
                hypotheses = node_update["hypotheses"]
                count = len(hypotheses) if isinstance(hypotheses, (list, tuple)) else 0
                await self._append_event(
                    record,
                    EventType.HYPOTHESIS_UPDATED,
                    {"count": count},
                )
            budget_keys = {
                "research_round",
                "tool_call_count",
                "model_call_count",
                "model_usage",
                "stop_reason",
            }
            if budget_keys.intersection(node_update):
                await self._append_event(
                    record,
                    EventType.BUDGET_UPDATED,
                    {"node": node},
                )

    async def _append_event(
        self,
        record: InvestigationRecord,
        event_type: EventType,
        data: Mapping[str, object],
    ) -> None:
        existing = await self._repository.list_events(record.investigation_id)
        sequence = len(existing) + 1
        sanitized = cast(dict[str, JsonValue], redact_value(dict(data)))
        await self._repository.append_event(
            InvestigationEvent(
                event_id=f"evt_{record.investigation_id.removeprefix('inv_')}_{sequence}",
                sequence=sequence,
                event_type=event_type,
                investigation_id=record.investigation_id,
                incident_id=record.incident_id,
                thread_id=record.thread_id,
                run_id=record.run_id,
                occurred_at=self._clock(),
                data=sanitized,
            )
        )

    @staticmethod
    def _models(value: object, model: type[Any]) -> tuple[Any, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, model))

    @staticmethod
    def _interrupt_value(tasks: Any) -> object | None:
        for task in tasks:
            for item in task.interrupts:
                return cast(object, item.value)
        return None

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _ensure_research_budget(state: InvestigationState) -> None:
        usage = state.get("model_usage", ModelUsage())
        exhausted = (
            state["research_round"] >= state["max_research_rounds"]
            or state.get("tool_call_count", 0) >= state["max_tool_calls"]
            or state.get("model_call_count", 0) >= state["max_model_calls"]
            or usage.input_tokens + usage.output_tokens >= state["max_estimated_tokens"]
        )
        if exhausted:
            raise ResourceConflictError("No investigation budget remains for additional research")
