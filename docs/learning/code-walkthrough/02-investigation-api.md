# 02 `investigations.py`：HTTP 与 SSE 适配层

源码：[src/incident_copilot/api/routes/investigations.py](../../../src/incident_copilot/api/routes/investigations.py)

## 四个公开入口

| 方法 | 路径 | 函数 | 作用 |
| --- | --- | --- | --- |
| POST | `/v1/investigations` | `create_investigation` | 创建异步调查 |
| GET | `/v1/investigations/{id}` | `get_investigation` | 读取安全任务投影 |
| GET | `/v1/investigations/{id}/events` | `stream_investigation_events` | 输出 SSE |
| POST | `/v1/investigations/{id}/resume` | `resume_investigation` | 接受或追加研究 |

路由只做协议转换。它不直接访问 Graph State，也不选择 Tool。

## 创建调查

```python
incident = payload.to_incident(f"inc_{uuid4().hex}")
record, created = await _service(request).create(
    incident=incident,
    options=payload.options,
    request_fingerprint=payload.fingerprint(),
    idempotency_key=idempotency_key,
)
response.headers["Location"] = (
    f"{settings.api_prefix}/v1/investigations/{record.investigation_id}"
)
return InvestigationResponse.from_record(record, replayed=not created)
```

逐行理解：

1. Pydantic 请求对象先转换为 `IncidentContext`，随机 ID 只标识事故。
2. `await` 暂停当前协程而不阻塞事件循环；Service 返回任务记录和是否新建。
3. 请求指纹和 `Idempotency-Key` 一起区分“安全重放”和“同键不同请求”。
4. `Location` 指向状态资源，符合异步任务的 `202 Accepted` 语义。
5. `replayed=not created` 告诉客户端这次是否复用了已有任务。

| 观察项 | 说明 |
| --- | --- |
| 输入来源 | HTTP JSON、Header 和 `request.app.state` 中的 Service |
| 输出去向 | HTTP 202、Location Header、`InvestigationResponse` |
| State 变化 | API 不直接改 State；Service 后台调用 `create_initial_state` |
| 下一节点 | Service 首次执行 Graph 后，从 `START` 进入 `parse_incident` |
| 后端类比 | Controller 接受 DTO，再调用 Application Service |
| 修改风险 | 改成同步等待会占用长连接并混淆 202 与完成语义 |

## 查询和恢复

```python
record = await _service(request).get(investigation_id)
return InvestigationResponse.from_record(record)
```

查询只返回任务投影，不暴露 checkpoint 原始 State 和完整工具载荷。

```python
record = await _service(request).resume(investigation_id, payload)
return InvestigationResponse.from_record(record)
```

`ResumeInvestigationRequest` 继承/符合 `HumanFeedback` 契约，只允许 Schema 声明的动作。锁、预算检查和 `Command(resume=...)` 都在 Service，避免 Controller 复制并发规则。

| 观察项 | 说明 |
| --- | --- |
| 输入来源 | URL 中的调查 ID、校验后的反馈 JSON |
| 输出去向 | 最新 `InvestigationRecord` 的响应投影 |
| State 变化 | 间接：反馈在 `human_review` 恢复点写入 `human_feedback` |
| 下一节点 | `accept` 后到 `END`；`request_more_research` 后到 `refine` |
| Python 语法 | `async def` 返回 awaitable；类型标注约束返回 DTO |
| 删除影响 | 绕过 Service 会丢失重复恢复保护和预算门禁 |

## SSE 建立与重连

```python
after_sequence = _parse_last_event_id(investigation_id, last_event_id)
return StreamingResponse(
    _event_stream(
        service,
        investigation_id,
        request,
        after_sequence=after_sequence,
        heartbeat_seconds=settings.sse_heartbeat_seconds,
    ),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

- `StreamingResponse` 消费异步迭代器，不会一次性构造完整响应体。
- `Last-Event-ID` 被解析为单调序号，只能属于当前调查。
- `X-Accel-Buffering: no` 避免代理把实时事件攒成大块。

## `_event_stream` 逐步执行

```python
while True:
    events = await service.repository.list_events(
        investigation_id, after_sequence=sequence
    )
    for event in events:
        yield _format_sse(event)
        sequence = event.sequence
    record = await service.get(investigation_id)
    if record.status in _STREAM_END_STATUSES:
        return
    if await request.is_disconnected():
        return
    events = await service.repository.wait_for_events(
        investigation_id,
        after_sequence=sequence,
        timeout_seconds=heartbeat_seconds,
    )
    if not events:
        yield ": heartbeat\n\n"
```

重要行解释：先补发已有事件，更新游标，再检查暂停/终态和客户端断开；没有新事件时输出 SSE 注释作为 heartbeat。`yield` 使函数成为异步生成器。

State 不在此处变化。输入是应用事件 Repository，输出是格式为 `id/event/data` 的文本。`waiting_review` 只结束本次流，不结束调查；恢复后客户端携带最后 ID 重连。若删除游标校验，客户端可能串读别的调查；若不在暂停时结束流，客户端会无限等待。

下一篇：[InvestigationService](03-investigation-service.md)。
