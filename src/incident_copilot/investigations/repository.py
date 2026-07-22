"""Repository 端口和确定性 Phase 5 内存 Adapter。"""

import asyncio
from typing import Protocol

from incident_copilot.core.exceptions import ResourceConflictError, ResourceNotFoundError
from incident_copilot.investigations.models import InvestigationEvent, InvestigationRecord


class InvestigationRepository(Protocol):
    """任务元数据及其仅追加事件日志的持久化契约。"""

    async def create(self, record: InvestigationRecord) -> tuple[InvestigationRecord, bool]: ...

    async def get(self, investigation_id: str) -> InvestigationRecord: ...

    async def update(
        self,
        record: InvestigationRecord,
        *,
        expected_version: int,
    ) -> InvestigationRecord: ...

    async def append_event(self, event: InvestigationEvent) -> None: ...

    async def list_events(
        self,
        investigation_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[InvestigationEvent, ...]: ...

    async def wait_for_events(
        self,
        investigation_id: str,
        *,
        after_sequence: int,
        timeout_seconds: float,
    ) -> tuple[InvestigationEvent, ...]: ...


class InMemoryInvestigationRepository:
    """本地开发和确定性测试使用的并发安全 Adapter。"""

    def __init__(self) -> None:
        self._records: dict[str, InvestigationRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._events: dict[str, list[InvestigationEvent]] = {}
        self._condition = asyncio.Condition()

    async def create(self, record: InvestigationRecord) -> tuple[InvestigationRecord, bool]:
        async with self._condition:
            if record.idempotency_key is not None:
                # 同一个 key 只有请求指纹也一致时才视为安全重放, 否则返回冲突。
                existing_id = self._idempotency.get(record.idempotency_key)
                if existing_id is not None:
                    existing = self._records[existing_id]
                    if existing.request_fingerprint != record.request_fingerprint:
                        raise ResourceConflictError(
                            "Idempotency key was already used for a different request",
                            details={"idempotency_key": record.idempotency_key},
                        )
                    return existing, False
            # 记录、空事件流和幂等索引在同一个 Condition 临界区内一起发布。
            self._records[record.investigation_id] = record
            self._events[record.investigation_id] = []
            if record.idempotency_key is not None:
                self._idempotency[record.idempotency_key] = record.investigation_id
            self._condition.notify_all()
            return record, True

    async def get(self, investigation_id: str) -> InvestigationRecord:
        async with self._condition:
            record = self._records.get(investigation_id)
            if record is None:
                raise ResourceNotFoundError(
                    "Investigation was not found",
                    details={"investigation_id": investigation_id},
                )
            return record

    async def update(
        self,
        record: InvestigationRecord,
        *,
        expected_version: int,
    ) -> InvestigationRecord:
        async with self._condition:
            current = self._records.get(record.investigation_id)
            if current is None:
                raise ResourceNotFoundError("Investigation was not found")
            # expected_version 实现乐观锁, 防止两个并发 resume 相互覆盖状态。
            if current.version != expected_version or record.version != expected_version + 1:
                raise ResourceConflictError("Investigation changed during the requested operation")
            self._records[record.investigation_id] = record
            self._condition.notify_all()
            return record

    async def append_event(self, event: InvestigationEvent) -> None:
        async with self._condition:
            events = self._events.get(event.investigation_id)
            if events is None:
                raise ResourceNotFoundError("Investigation was not found")
            expected_sequence = len(events) + 1
            # 事件日志只允许追加连续序号, SSE 客户端才能使用 Last-Event-ID 可靠续传。
            if event.sequence != expected_sequence:
                raise ResourceConflictError("Investigation event sequence is not monotonic")
            events.append(event)
            self._condition.notify_all()

    async def list_events(
        self,
        investigation_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[InvestigationEvent, ...]:
        async with self._condition:
            events = self._events.get(investigation_id)
            if events is None:
                raise ResourceNotFoundError("Investigation was not found")
            return tuple(event for event in events if event.sequence > after_sequence)

    async def wait_for_events(
        self,
        investigation_id: str,
        *,
        after_sequence: int,
        timeout_seconds: float,
    ) -> tuple[InvestigationEvent, ...]:
        async with self._condition:
            if investigation_id not in self._events:
                raise ResourceNotFoundError("Investigation was not found")

            def available() -> bool:
                return len(self._events[investigation_id]) > after_sequence

            try:
                # Condition 在新事件写入时唤醒等待者, 避免 SSE 使用高频轮询浪费 CPU。
                await asyncio.wait_for(
                    self._condition.wait_for(available),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                # 空元组表示心跳窗口内没有新事件, 不是 Repository 失败。
                return ()
            return tuple(
                event for event in self._events[investigation_id] if event.sequence > after_sequence
            )
