"""带有校验、超时、重试和遥测能力的工具白名单 Registry。

Registry 是 Graph 与 Provider 之间的统一安全边界。Graph 只能调用已注册
工具; Registry 在进入 Provider 前校验参数和预算, 在返回 Graph 前校验证据来源、服务、
时间范围和数量。Provider 异常会被转换为稳定 ToolError, 不把厂商细节泄漏给编排层。
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from typing import Generic, TypeVar, cast

from pydantic import ValidationError

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.core.telemetry import trace_async
from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.tools.exceptions import (
    ProviderError,
    ProviderMalformedResponseError,
    ProviderTimeoutError,
    ToolBudgetExceededError,
    ToolExecutionError,
    ToolInvalidArgumentsError,
    ToolNotFoundError,
    ToolRegistrationError,
)
from incident_copilot.tools.schemas import (
    GetServiceTopologyInput,
    QueryContext,
    SearchRunbooksInput,
    SearchSimilarIncidentsInput,
    TimeRangeToolInput,
    ToolExecutionResult,
    ToolInput,
)

# ToolRegistry 使用的模块日志记录器。
logger = logging.getLogger(__name__)

# 所有工具输入都必须继承的泛型类型变量。
InputT = TypeVar("InputT", bound=ToolInput)
# 工具处理器的统一异步函数签名。
ToolHandler = Callable[[InputT, QueryContext], Awaitable[Sequence[Evidence]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition(Generic[InputT]):
    """保存一个白名单工具及其 Provider 调用策略。

    定义把工具名、Pydantic 输入、异步 handler、允许来源和执行策略绑定在一起。
    Graph 计划只能引用这里存在的 name。
    """

    # Graph 计划引用的稳定白名单工具名。
    name: str
    # 调用 Provider 前验证 arguments 的 Pydantic 模型类型。
    input_model: type[InputT]
    # 真正把已校验输入传给 Provider 的异步函数。
    handler: ToolHandler[InputT]
    # 该工具唯一允许返回的 Evidence 来源类别。
    expected_sources: frozenset[SourceType]
    # 单次尝试允许等待的最大秒数。
    timeout_seconds: float = 2.0
    # 第一次失败后最多允许追加的重试次数。
    max_retries: int = 1

    def __post_init__(self) -> None:
        # 注册时一次性验证策略边界, execute 热路径无需反复检查静态配置。
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", self.name) is None:
            raise ValueError("tool name must contain lowercase letters, digits, and underscores")
        if not self.expected_sources:
            raise ValueError("tool must declare at least one expected evidence source")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("tool timeout must be greater than 0 and at most 30 seconds")
        if self.max_retries < 0 or self.max_retries > 3:
            raise ValueError("tool max_retries must be between 0 and 3")


class ToolRegistry:
    """通过统一策略和错误边界执行已注册工具。

    Registry 不维护整个调查的共享计数;调查级预算由 Graph State 负责。它只执行
    当前调用的 remaining budget、deadline、单次 timeout 和有限 retry。
    """

    def __init__(
        self,
        *,
        retry_backoff_seconds: float = 0.01,
        clock: Clock = utc_now,
    ) -> None:
        if retry_backoff_seconds < 0 or retry_backoff_seconds > 1:
            raise ValueError("retry_backoff_seconds must be between 0 and 1")
        self._retry_backoff_seconds = retry_backoff_seconds
        self._clock = clock
        self._tools: dict[str, ToolDefinition[ToolInput]] = {}

    @property
    def tool_names(self) -> tuple[str, ...]:
        """返回稳定的工具发现列表,但不暴露 Provider 实例。"""
        return tuple(sorted(self._tools))

    @property
    def attempt_limits(self) -> dict[str, int]:
        """返回每个工具包含首次调用在内的可信物理尝试上限。"""
        return {name: definition.max_retries + 1 for name, definition in self._tools.items()}

    def register(self, definition: ToolDefinition[InputT]) -> None:
        """注册一个工具定义,并拒绝意外的同名替换。

        禁止静默覆盖同名工具,防止装配顺序意外改变实际 Provider。
        """
        if definition.name in self._tools:
            raise ToolRegistrationError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = cast(ToolDefinition[ToolInput], definition)

    @trace_async("incident_copilot.tool.execute", component="tool")
    async def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        context: QueryContext,
    ) -> ToolExecutionResult:
        """在预算、超时和重试限制内校验并执行工具。

        执行顺序:白名单 → 调用预算 → Pydantic 参数 → deadline/timeout → Provider
        → Evidence 边界校验 → 结构化结果。只有 retryable 错误才会重试。
        """
        # 第一道边界是工具名白名单: 未注册名称在解析参数前就被拒绝。
        definition = self._tools.get(name)
        if definition is None:
            raise ToolNotFoundError(f"unknown tool: {name}")
        if context.remaining_tool_attempts < 1:
            raise ToolBudgetExceededError("tool attempt budget exhausted")

        try:
            # 外部或模型生成的 arguments 必须先收敛到具体工具 Schema。
            tool_input = definition.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInvalidArgumentsError(f"invalid arguments for tool: {name}") from exc

        started = perf_counter()
        logger.info(
            "tool.started",
            extra={"tool_name": name, "correlation_id": context.correlation_id},
        )

        attempts = 0
        # 工具自身重试策略与 Graph 分配额度取更小值, 两层预算缺一不可。
        max_attempts = min(definition.max_retries + 1, context.remaining_tool_attempts)
        while attempts < max_attempts:
            attempts += 1
            remaining_seconds = (context.deadline - self._clock()).total_seconds()
            if remaining_seconds <= 0:
                failure: ProviderError = ProviderTimeoutError(
                    "tool deadline exceeded",
                    provider_name="tool-registry",
                    operation=name,
                )
            else:
                # 单次 timeout 不能超过调用方传入的全局剩余 deadline。
                attempt_timeout = min(definition.timeout_seconds, remaining_seconds)
                try:
                    # wait_for 覆盖完整 handler, 包括 Provider 网络调用和响应解析时间。
                    evidence = await asyncio.wait_for(
                        definition.handler(tool_input, context),
                        timeout=attempt_timeout,
                    )
                    checked_evidence = self._validate_evidence(definition, evidence, tool_input)
                except TimeoutError as exc:
                    failure = ProviderTimeoutError(
                        "provider call timed out",
                        provider_name="tool-registry",
                        operation=name,
                    )
                    failure.__cause__ = exc
                except ProviderError as exc:
                    # 已分类 ProviderError 保留其类别和 retryable 语义, 供下方重试决策使用。
                    failure = exc
                except Exception as exc:
                    # 未知异常统一包成不可重试 ProviderError, 防止内部细节穿透工具边界。
                    failure = ProviderError(
                        "provider raised an unexpected error",
                        provider_name="tool-registry",
                        operation=name,
                    )
                    failure.__cause__ = exc
                else:
                    duration_ms = int((perf_counter() - started) * 1_000)
                    result = ToolExecutionResult(
                        tool_name=name,
                        evidence=checked_evidence,
                        attempts=attempts,
                        duration_ms=duration_ms,
                    )
                    logger.info(
                        "tool.completed",
                        extra={
                            "tool_name": name,
                            "correlation_id": context.correlation_id,
                            "attempts": attempts,
                            "result_count": len(checked_evidence),
                            "duration_ms": duration_ms,
                        },
                    )
                    return result

            if failure.retryable and attempts < max_attempts:
                # 退避也必须装得进剩余 deadline,否则立即返回归一化失败。
                backoff = self._retry_backoff_seconds * (2 ** (attempts - 1))
                remaining_seconds = (context.deadline - self._clock()).total_seconds()
                if backoff < remaining_seconds:
                    await asyncio.sleep(backoff)
                    continue

            duration_ms = int((perf_counter() - started) * 1_000)
            logger.warning(
                "tool.failed",
                extra={
                    "tool_name": name,
                    "correlation_id": context.correlation_id,
                    "attempts": attempts,
                    "duration_ms": duration_ms,
                    "error_category": failure.category.value,
                },
            )
            # Graph 只接收稳定 ToolExecutionError, 原始异常通过 cause 链保留给日志与调试。
            raise ToolExecutionError(
                f"tool execution failed: {name}",
                tool_name=name,
                category=failure.category,
                attempts=attempts,
                retryable=failure.retryable,
            ) from failure

        raise AssertionError("bounded retry loop exited unexpectedly")  # pragma: no cover

    @staticmethod
    def _validate_evidence(
        definition: ToolDefinition[ToolInput],
        evidence: Sequence[Evidence],
        tool_input: ToolInput,
    ) -> tuple[Evidence, ...]:
        """验证 Provider 结果仍位于调用者请求的来源、服务和时间边界内。

        Provider 是外部边界, 即使返回了 ``Evidence`` 实例也不能默认可信。该检查防止
        错服务、越界时间窗、错误来源或超量数据进入 Graph State。
        """
        bounded_inputs = (
            TimeRangeToolInput,
            GetServiceTopologyInput,
            SearchRunbooksInput,
            SearchSimilarIncidentsInput,
        )
        result_limit = tool_input.limit if isinstance(tool_input, bounded_inputs) else 50
        # 先检查整体数量, 避免恶意或故障 Provider 返回大集合后再逐条处理。
        if len(evidence) > result_limit:
            raise ProviderMalformedResponseError(
                "provider returned more evidence than requested",
                provider_name="tool-registry",
                operation=definition.name,
            )
        checked: list[Evidence] = []
        for item in evidence:
            # Evidence 已由 Pydantic 构造仍不等于属于本次查询, 下面继续校验跨对象约束。
            if not isinstance(item, Evidence):
                raise ProviderMalformedResponseError(
                    "provider returned a non-Evidence value",
                    provider_name="tool-registry",
                    operation=definition.name,
                )
            if item.source_type not in definition.expected_sources:
                raise ProviderMalformedResponseError(
                    "provider returned an unexpected evidence source",
                    provider_name=item.source_name,
                    operation=definition.name,
                )
            if item.service != tool_input.service:
                raise ProviderMalformedResponseError(
                    "provider evidence service is outside the requested scope",
                    provider_name=item.source_name,
                    operation=definition.name,
                )
            if item.timestamp is None and item.start_time is None:
                raise ProviderMalformedResponseError(
                    "provider evidence must include a timestamp or time window",
                    provider_name=item.source_name,
                    operation=definition.name,
                )
            if isinstance(tool_input, TimeRangeToolInput) and not ToolRegistry._overlaps_window(
                item, tool_input.start_time, tool_input.end_time
            ):
                raise ProviderMalformedResponseError(
                    "provider evidence is outside the requested time window",
                    provider_name=item.source_name,
                    operation=definition.name,
                )
            if isinstance(tool_input, GetServiceTopologyInput) and (
                item.timestamp is None or item.timestamp > tool_input.at_time
            ):
                raise ProviderMalformedResponseError(
                    "provider topology evidence is newer than the requested time",
                    provider_name=item.source_name,
                    operation=definition.name,
                )
            if isinstance(tool_input, SearchSimilarIncidentsInput):
                # 历史事故必须早于当前事故且落在 lookback 窗口内, 防止未来数据泄漏。
                earliest = tool_input.before_time - timedelta(days=tool_input.lookback_days)
                if (
                    item.timestamp is None
                    or not earliest <= item.timestamp < tool_input.before_time
                ):
                    raise ProviderMalformedResponseError(
                        "provider incident evidence is outside the requested lookback",
                        provider_name=item.source_name,
                        operation=definition.name,
                    )
            checked.append(item)
        return tuple(checked)

    @staticmethod
    def _overlaps_window(item: Evidence, start_time: datetime, end_time: datetime) -> bool:
        if item.timestamp is not None:
            return start_time <= item.timestamp <= end_time
        return (
            item.start_time is not None
            and item.end_time is not None
            and item.start_time <= end_time
            and item.end_time >= start_time
        )
