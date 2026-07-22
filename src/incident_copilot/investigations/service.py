"""支持 checkpoint 的调查生命周期和安全事件投影。

这是 HTTP 层与 LangGraph 之间的应用服务。它负责调查任务状态机、后台
``asyncio.Task``、事件投影、幂等创建和 checkpoint 恢复;Graph 仍负责证据调查本身。
理解本文件时要区分三类状态:Repository 中的任务状态、Graph checkpoint 中的工作流
State,以及发送给 SSE 客户端的安全事件。
"""

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import JsonValue

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.core.exceptions import ResourceConflictError, ResourceNotFoundError
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

# 调查 Service 使用的模块日志记录器。
logger = logging.getLogger(__name__)
# 表示后台 Graph 已暂停或结束、当前没有继续运行的任务状态。
_QUIESCENT_STATUSES = {
    InvestigationStatus.WAITING_REVIEW,
    InvestigationStatus.COMPLETED,
    InvestigationStatus.FAILED,
}


class InvestigationService:
    """管理任务状态转换,由 LangGraph 管理 checkpoint 中的工作流状态。

    Service 管理 pending/running/waiting_review/completed/failed 等应用状态;
    LangGraph 通过 ``thread_id`` 管理节点执行位置与 InvestigationState。两者职责不同,
    因此 PostgreSQL checkpoint 不能替代持久化的任务/事件 Repository。
    """

    def __init__(
        self,
        *,
        graph: InvestigationGraph,
        repository: InvestigationRepository,
        clock: Clock | None = None,
    ) -> None:
        self._graph = graph
        self._repository = repository
        self._clock = clock or utc_now
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def repository(self) -> InvestigationRepository:
        """向 SSE 传输层提供 Repository 端口。"""
        return self._repository

    async def aclose(self) -> None:
        """在依赖关闭前取消并等待所有进程内执行任务。

        应用关闭时先取消并 await 所有进程内任务,避免 Graph 仍在使用已关闭的
        Checkpointer 或 Provider 资源。
        """
        # 先复制任务列表再取消, done callback 修改原字典时不会影响当前遍历。
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._locks.clear()

    async def create(
        self,
        *,
        incident: IncidentContext,
        options: InvestigationOptions,
        request_fingerprint: str,
        idempotency_key: str | None,
    ) -> tuple[InvestigationRecord, bool]:
        """幂等创建调查并调度一次后台执行。

        Repository 以 Idempotency-Key 和请求指纹决定是新建还是重放。只有真正
        新建的记录才会产生 queued 事件并启动后台 Graph,避免同一请求重复调查。
        """
        now = self._clock()
        # 同一随机 resource_key 派生 investigation_id 和 thread_id, 使进程重启后可反推快照。
        resource_key = uuid4().hex
        investigation_id = f"inv_{resource_key}"
        record = InvestigationRecord(
            investigation_id=investigation_id,
            incident_id=incident.incident_id,
            thread_id=f"thread_{resource_key}",
            run_id=f"run_{uuid4().hex}",
            incident=incident,
            options=options,
            request_fingerprint=request_fingerprint,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        # Repository 原子处理 Idempotency-Key; Service 只为真正新建的记录启动一次后台任务。
        stored, created = await self._repository.create(record)
        if not created:
            return stored, False
        await self._append_event(stored, EventType.INVESTIGATION_QUEUED, {"status": "pending"})
        # HTTP 请求立即返回 202;耗时 Graph 在独立 asyncio.Task 中推进。
        self._start_task(stored.investigation_id, self._run_initial(stored.investigation_id))
        return stored, True

    async def get(self, investigation_id: str) -> InvestigationRecord:
        """返回任务元数据,必要时从 checkpoint 重建缺失的暂停记录。

        内存 Repository 在进程重建后可能为空;此时通过稳定 ID 推导 thread ID,
        从 checkpoint 重建最小任务投影。历史 SSE 事件不会因此恢复。
        """
        try:
            return await self._repository.get(investigation_id)
        except ResourceNotFoundError:
            return await self._recover_from_checkpoint(investigation_id)

    async def resume(
        self,
        investigation_id: str,
        feedback: HumanFeedback,
    ) -> InvestigationRecord:
        """原子认领暂停的 checkpoint 并调度恢复命令。

        锁保证同一调查只能被一次恢复请求认领。读取 Graph 快照后先验证追加研究
        仍有预算,再把任务改为 running,最后用 ``Command(resume=feedback)`` 继续同一个
        ``thread_id``。重复恢复会因为状态不再是 waiting_review 而得到冲突。
        """
        lock = self._locks.setdefault(investigation_id, asyncio.Lock())
        async with lock:
            # 进程内锁先串行化同一调查的恢复请求, Repository version 再提供第二层保护。
            record = await self.get(investigation_id)
            if record.status is not InvestigationStatus.WAITING_REVIEW:
                raise ResourceConflictError(
                    "Investigation is not waiting for review",
                    details={"status": record.status.value},
                )
            config = self._config(record.thread_id)
            # 是否还能追加研究以 checkpoint 中的真实累计预算为准, 不信任客户端状态。
            snapshot = await self._graph.aget_state(config)
            state = cast(InvestigationState, snapshot.values)
            if feedback.action is ReviewAction.REQUEST_MORE_RESEARCH:
                # 人工反馈也不能突破研究轮数、工具、模型和 Token 等硬预算。
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
                # 新一轮获得新的墙钟 deadline, 但研究轮数和各类调用累计值不会重置。
                update = {
                    "deadline_at": now + timedelta(seconds=record.options.timeout_seconds),
                    "deadline_exceeded": False,
                }
            command: Command[Any] = Command(
                resume=feedback.model_dump(mode="json"),
                update=update,
            )
            # resume 仍放入后台任务,API 只确认恢复请求已被接受。
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
        """等待任务进入暂停或终止状态。

        这是受控轮询辅助方法,不是生产任务队列;主要供脚本和测试等待可观察结果。
        """
        # 等待超时使用事件循环单调时钟, 不受系统时间校准或时区变化影响。
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            record = await self._repository.get(investigation_id)
            if record.status in _QUIESCENT_STATUSES:
                return record
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("investigation did not reach a quiescent state")
            await asyncio.sleep(0.01)

    def _start_task(
        self,
        investigation_id: str,
        coroutine: Coroutine[Any, Any, None],
    ) -> None:
        """登记一个进程内任务,并在结束时观察异常和清理引用。"""
        task = asyncio.create_task(coroutine, name=f"investigation:{investigation_id}")
        self._tasks[investigation_id] = task

        def remove(completed: asyncio.Task[None]) -> None:
            # done callback 同时清理引用和读取异常, 避免后台 Task 产生未观察异常警告。
            if self._tasks.get(investigation_id) is completed:
                self._tasks.pop(investigation_id, None)
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.error(
                    "Investigation background task failed",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"investigation_id": investigation_id},
                )

        task.add_done_callback(remove)

    async def _recover_from_checkpoint(self, investigation_id: str) -> InvestigationRecord:
        """从稳定 investigation/thread 映射和 Graph 快照恢复最小任务元数据。"""
        prefix = "inv_"
        resource_key = investigation_id.removeprefix(prefix)
        if (
            not investigation_id.startswith(prefix)
            or len(resource_key) != 32
            or any(character not in "0123456789abcdef" for character in resource_key)
        ):
            raise ResourceNotFoundError(
                "Investigation was not found",
                details={"investigation_id": investigation_id},
            )
        thread_id = f"thread_{resource_key}"
        # 业务 Repository 丢失时只能恢复 checkpoint 中存在的数据, 历史 SSE 事件不会伪造。
        snapshot = await self._graph.aget_state(self._config(thread_id))
        state = cast(InvestigationState, snapshot.values)
        incident = state.get("incident")
        if incident is None:
            raise ResourceNotFoundError(
                "Investigation was not found",
                details={"investigation_id": investigation_id},
            )
        interrupt_value = self._interrupt_value(snapshot.tasks)
        review_request = (
            HumanReviewRequest.model_validate(interrupt_value)
            if interrupt_value is not None
            else None
        )
        # interrupt、已结束且有报告、仍可运行三种快照分别映射为业务任务状态。
        status = (
            InvestigationStatus.WAITING_REVIEW
            if review_request is not None
            else InvestigationStatus.COMPLETED
            if not snapshot.next and state.get("final_report") is not None
            else InvestigationStatus.PENDING
        )
        started_at = state.get("started_at", self._clock())
        # 从 State 中重建原预算, 让恢复后的 resume 继续遵守创建时的限制。
        options = InvestigationOptions(
            max_research_rounds=state.get("max_research_rounds", 2),
            max_tool_calls=state.get("max_tool_calls", 14),
            max_tool_attempts=state.get("max_tool_attempts", 28),
            max_parallel_tools=state.get("max_parallel_tools", 7),
            max_model_calls=state.get("max_model_calls", 20),
            max_estimated_tokens=state.get("max_estimated_tokens", 20_000),
            timeout_seconds=max(
                0.001,
                (state.get("deadline_at", started_at) - started_at).total_seconds(),
            ),
        )
        recovered = InvestigationRecord(
            investigation_id=investigation_id,
            incident_id=incident.incident_id,
            thread_id=thread_id,
            run_id=f"run_{uuid4().hex}",
            status=status,
            incident=incident,
            options=options,
            request_fingerprint="0" * 64,
            report=state.get("final_report"),
            review_request=review_request,
            created_at=started_at,
            updated_at=self._clock(),
        )
        stored, _ = await self._repository.create(recovered)
        return stored

    async def _run_initial(self, investigation_id: str) -> None:
        """把新任务切换到 running,构造初始 State 并进入统一执行路径。"""
        try:
            record = await self._repository.get(investigation_id)
            # 先持久化 running 和 started 事件, 再进入 Graph, 客户端不会看到无状态后台任务。
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
            initial = create_initial_state(
                running.incident, options=running.options, clock=self._clock
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Investigation initialization failed",
                extra={"investigation_id": investigation_id},
            )
            await self._mark_failed(investigation_id)
            return
        await self._execute(investigation_id, initial)

    async def _execute(
        self,
        investigation_id: str,
        graph_input: InvestigationState | Command[Any],
    ) -> None:
        """流式执行 Graph,把节点增量投影为事件,再同步暂停或完成状态。

        Graph 的 ``astream(..., stream_mode='updates')`` 返回节点增量;Service 不把这些
        内部对象原样暴露,而是抽取允许的节点、工具、Evidence 和预算信息形成 SSE 事件。
        """
        try:
            record = await self._repository.get(investigation_id)
            config = self._config(record.thread_id)
            async for update in self._graph.astream(
                graph_input,
                config,
                stream_mode="updates",
            ):
                if isinstance(update, Mapping):
                    # Graph 增量先投影为公开事件, 不把内部 State 或模型载荷直接推给 SSE。
                    await self._project_graph_update(
                        record,
                        cast(Mapping[object, object], update),
                    )
            # 流结束可能表示正常完成, 也可能表示 interrupt 暂停, 必须读取最终快照区分。
            snapshot = await self._graph.aget_state(config)
            latest = await self._repository.get(investigation_id)
            interrupt_value = self._interrupt_value(snapshot.tasks)
            values = cast(InvestigationState, snapshot.values)
            report = values.get("final_report")
            if interrupt_value is not None:
                # 存在 interrupt 表示 Graph 已安全暂停,而不是执行失败。
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
            if report is None:
                # Graph 没有报告不能伪装成 completed,统一进入 failed 路径。
                raise RuntimeError("graph completed without a final report")
            # 只有存在通过领域 Schema 校验的 final_report 才能进入 completed。
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
                {
                    "report_id": report.report_id,
                    "tool_call_count": report.investigation_stats.tool_call_count,
                    "tool_attempt_count": report.investigation_stats.tool_attempt_count,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Investigation execution failed",
                extra={"investigation_id": investigation_id},
            )
            await self._mark_failed(investigation_id)

    async def _mark_failed(self, investigation_id: str) -> None:
        # 对外只保存稳定安全消息; 详细堆栈已经由调用处写入服务端日志。
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
        """把 Graph 内部节点更新转换成稳定、脱敏且可排序的应用事件。"""
        for raw_node, raw_value in update.items():
            node = str(raw_node)
            # interrupt 有专门的 REVIEW_REQUIRED 事件, 不把内部伪节点重复暴露给客户端。
            if node == "__interrupt__":
                continue
            await self._append_event(record, EventType.NODE_COMPLETED, {"node": node})
            if not isinstance(raw_value, Mapping):
                continue
            node_update = cast(Mapping[str, object], raw_value)
            for step in self._models(node_update.get("completed_steps"), StepResult):
                # StepResult 已经把 Provider 异常压缩成状态与尝试数, 事件中不泄漏原始错误。
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
                        "attempts": step.attempts,
                        "evidence_ids": list(step.evidence_ids),
                    },
                )
            for evidence in self._models(node_update.get("evidence"), EvidenceRef):
                # SSE 只携带 EvidenceRef 摘要和 Citation, 不发送 Provider 的完整 content。
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
                "tool_attempt_count",
                "model_call_count",
                "model_usage",
                "stop_reason",
            }
            if budget_keys.intersection(node_update):
                # Reducer 接收的是增量, 因此事件字段明确标记 delta 而不是累计总数。
                await self._append_event(
                    record,
                    EventType.BUDGET_UPDATED,
                    {
                        "node": node,
                        **(
                            {"logical_tool_steps_delta": node_update["tool_call_count"]}
                            if "tool_call_count" in node_update
                            else {}
                        ),
                        **(
                            {"physical_tool_attempts_delta": node_update["tool_attempt_count"]}
                            if "tool_attempt_count" in node_update
                            else {}
                        ),
                    },
                )

    async def _append_event(
        self,
        record: InvestigationRecord,
        event_type: EventType,
        data: Mapping[str, object],
    ) -> None:
        """生成调查内单调序号,并在写入 Repository 前递归脱敏载荷。"""
        existing = await self._repository.list_events(record.investigation_id)
        # 序号在 Repository 当前事件尾部生成; Adapter 会再次验证单调性以发现并发冲突。
        sequence = len(existing) + 1
        # 所有嵌套数据在持久化和 streaming 前统一递归脱敏。
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
        # 当前 Graph 只允许一个审核 interrupt, 找到首个值即可交给 Pydantic 校验。
        for task in tasks:
            for item in task.interrupts:
                return cast(object, item.value)
        return None

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        """构造 LangGraph checkpoint 识别当前执行线程所需的配置。"""
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _ensure_research_budget(state: InvestigationState) -> None:
        """在接受“追加研究”反馈前执行不可绕过的确定性预算检查。"""
        usage = state.get("model_usage", ModelUsage())
        # 任一硬预算耗尽都拒绝恢复, 人工审核不能成为绕过自动安全边界的后门。
        exhausted = (
            state["research_round"] >= state["max_research_rounds"]
            or state.get("tool_call_count", 0) >= state["max_tool_calls"]
            or state.get("tool_attempt_count", 0) >= state["max_tool_attempts"]
            or state.get("model_call_count", 0) >= state["max_model_calls"]
            or usage.input_tokens + usage.output_tokens >= state["max_estimated_tokens"]
        )
        if exhausted:
            raise ResourceConflictError("No investigation budget remains for additional research")
