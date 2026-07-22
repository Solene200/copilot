"""仅供作品集演示使用的确定性辅助函数。"""

import asyncio
from datetime import UTC, datetime, timedelta

from incident_copilot.domain.evidence import Evidence
from incident_copilot.fixtures.schemas import IncidentFixture
from incident_copilot.tools.providers import PrometheusMetricsProvider
from incident_copilot.tools.schemas import QueryContext, QueryMetricsInput


async def wait_for_metric(
    provider: PrometheusMetricsProvider,
    *,
    timeout_seconds: float,
) -> tuple[Evidence, ...]:
    """演示 Graph 启动前要求观察到两次成功抓取。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_error: Exception | None = None
    consecutive_successes = 0
    while loop.time() < deadline:
        # 每次轮询使用靠近当前时间的窗口, 避免 Prometheus 演示查询固定历史数据。
        now = datetime.now(UTC)
        query = QueryMetricsInput(
            service="payment-service",
            start_time=now - timedelta(minutes=20),
            end_time=now,
            metric_name="db.pool.utilization",
            aggregation="max",
            limit=5,
        )
        context = QueryContext(
            correlation_id="phase7-observability-demo",
            deadline=now + timedelta(seconds=5),
            remaining_tool_attempts=1,
        )
        try:
            evidence = await provider.query(query, context)
        except Exception as exc:
            # 任一次失败都会清零连续成功计数, 防止偶发可用被误判为数据源稳定。
            last_error = exc
            consecutive_successes = 0
        else:
            if evidence:
                consecutive_successes += 1
                # 连续两次拿到非空证据后才开始演示 Graph, 降低服务刚启动时的抖动。
                if consecutive_successes >= 2:
                    return evidence
            else:
                consecutive_successes = 0
            last_error = None
        await asyncio.sleep(2)
    if last_error is not None:
        raise TimeoutError("Prometheus metric did not become available") from last_error
    raise TimeoutError("Prometheus metric did not become available")


def shift_fixture_to_now(fixture: IncidentFixture, reference_end: datetime) -> IncidentFixture:
    """把 Fixture 时间移动到实时指标窗口,但不改变证据陈述。"""
    # 所有事故与证据时间使用同一个偏移量, 相对因果顺序保持不变。
    delta = reference_end - fixture.incident.end_time

    def shifted(value: datetime | None) -> datetime | None:
        return value + delta if value is not None else None

    evidence: list[Evidence] = []
    for item in fixture.evidence:
        # model_copy 保留脱敏内容和稳定 ID, 只移动时间及 Citation 获取时间。
        evidence.append(
            item.model_copy(
                update={
                    "timestamp": shifted(item.timestamp),
                    "start_time": shifted(item.start_time),
                    "end_time": shifted(item.end_time),
                    "collected_at": shifted(item.collected_at),
                    "citation": item.citation.model_copy(
                        update={"retrieved_at": shifted(item.citation.retrieved_at)}
                    ),
                }
            )
        )
    incident = fixture.incident.model_copy(
        update={
            "incident_id": f"inc_payment_live_{reference_end.strftime('%Y%m%d_%H%M%S')}",
            "start_time": fixture.incident.start_time + delta,
            "end_time": reference_end,
            "created_at": reference_end,
        }
    )
    return fixture.model_copy(update={"incident": incident, "evidence": tuple(evidence)})
