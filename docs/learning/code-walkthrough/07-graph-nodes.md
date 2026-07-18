# 07 `nodes.py`：十个核心 Node

源码：[src/incident_copilot/graph/nodes.py](../../../src/incident_copilot/graph/nodes.py)

## Node 总表

| Node | 主要读取 | 主要写入 | 下一步由谁确定 |
| --- | --- | --- | --- |
| `parse_incident` | incident、deadline | deadline/stop reason | `route_after_parse` |
| `build_investigation_plan` | incident、预算、历史查询 | plan、pending、model 增量 | `dispatch_evidence_collection` |
| `collect_evidence` | current step、incident、deadline | step、evidence/error、tool 增量 | 固定到 aggregate |
| `aggregate_evidence` | reducer 后计数、预算 | deadline/stop reason | `dispatch_after_aggregate` |
| `generate_hypotheses` | evidence、incident | hypotheses、model 增量 | 固定到 verify |
| `verify_hypotheses` | evidence、hypotheses | 校验后的 hypotheses | 固定到 judge |
| `judge_evidence` | 来源、假设、预算 | sufficiency、queries、stop | `route_after_judge` |
| `refine_investigation` | gap、feedback、历史 | 新 plan、round、model 增量 | Send dispatch |
| `generate_report` | 全部调查结果 | final report、model 增量 | `route_after_report` |
| `human_review` | report | feedback、review、重置字段 | `Command.goto` |

## 边界 Node：`parse_incident`

```python
deadline_exceeded = self._clock() >= state["deadline_at"]
update: InvestigationState = {"deadline_exceeded": deadline_exceeded}
if deadline_exceeded:
    update["stop_reason"] = StopReason.DEADLINE_EXCEEDED
return update
```

Incident 已在 API/领域层校验，此处只检查 Graph 必需服务和 deadline。输入来自初始 State，输出最小增量。路由随后选择 plan 或直接生成受限报告。删除 deadline 检查会让过期请求仍调用工具和模型。

## 计划和追加研究

`build_investigation_plan` 与 `refine_investigation` 复用 `_plan_update`。后者先计算 `next_round`，再由单一 writer 写轮次，避免并行分支竞争。

```python
if step.tool_name not in allowed:
    continue
query_key = stable_query_key(step.tool_name, step.arguments)
if query_key in completed_queries or query_key in seen_queries:
    continue
...
"step_id": f"step_r{round_number}_{ordinal}_{query_key[:12]}",
"query_key": query_key,
"round_number": round_number,
```

模型只建议工具和参数，可信代码重新计算 identity、过滤未知工具和重复查询。输入是结构化 `PlanOutput`，输出是 plan/pending steps。下一步由 Builder 的 Send dispatcher 决定。若信任模型提供的 step ID，重放幂等和 Evaluation 工具匹配都会失真。

## `collect_evidence`：一个分支只执行一步

```python
context = QueryContext(
    correlation_id=f"{state['incident'].incident_id}:{step.step_id}",
    deadline=state["deadline_at"],
    remaining_tool_calls=1,
)
result = await self._registry.execute(step.tool_name, step.arguments, context)
```

`current_step` 由 Send scoped State 注入。Registry 再执行 Schema、白名单、timeout、retry 和输出校验。成功时完整 Evidence 转为轻量 `EvidenceRef`；分支只返回计数增量 `1`。

```python
return {
    "completed_steps": (step_result,),
    "evidence": refs,
    "tool_call_count": 1,
    "tool_success_count": 1,
}
```

失败不是吞掉异常：它转换成 `InvestigationError` 和 FAILED StepResult，并写 failure 增量。Reducer 在 barrier 前合并所有分支。下一节点固定是 aggregate。后端类比是 worker 将成功/失败都写成可审计结果；若让 ToolError 冒泡，单一 Provider 失败会取消整批调查。

## `aggregate_evidence`：并行汇合后的预算检查

```python
projected = state.copy()
projected["deadline_exceeded"] = deadline_exceeded
reason = budget_stop_reason(projected)
if reason is not None:
    update["stop_reason"] = reason
```

进入该节点前 Reducer 已合并 evidence、steps 和计数。`copy()` 构造“如果写入 deadline 后”的投影，用同一纯预算函数计算停止原因。输出只含 deadline/stop。下一步可能发送下一批、生成假设或报告。

## 生成与验证 Hypothesis

`generate_hypotheses` 只接受通过 `HypothesesOutput` 的模型结果；失败时使用确定性 Fake fallback。`verify_hypotheses` 才是可信外键和置信度门禁：

```python
supporting_ids = tuple(
    item for item in hypothesis.supporting_evidence_ids if item in evidence_by_id
)
supporting_sources = {evidence_by_id[item].source_type for item in supporting_ids}
status = (
    HypothesisStatus.SUPPORTED
    if supporting_ids and len(supporting_sources) >= 2
    else HypothesisStatus.INCONCLUSIVE
)
```

模型伪造的 Evidence ID 被删除；至少两个独立来源才能标记 SUPPORTED，否则置信度上限为 0.55，无支持证据则上限 0.2。输出覆盖 `hypotheses`，下一节点固定 judge。删除 verify 会让模型自由文本越过证据完整性约束。

## `judge_evidence`：模型意见与确定性规则相交

```python
supported = any(
    item.status is HypothesisStatus.SUPPORTED for item in state.get("hypotheses", ())
)
sources = {item.source_type for item in state.get("evidence", ())}
sufficient = output.sufficient and supported and len(sources) >= 2
```

即使模型说 sufficient，也必须存在已验证假设和至少两类来源。Node 写充分性、原因、下一查询、模型用量和 stop reason；它只“准备”路由条件，真正下一节点仍由 `route_after_judge` 决定。

## `_call_structured`：有界模型调用

```python
max_attempts = 0 if tokens_exhausted or deadline_exceeded else min(2, remaining)
for attempt in range(1, max_attempts + 1):
    response = await asyncio.wait_for(
        self._model.complete(context), timeout=remaining_seconds
    )
    usage = add_usage(usage, response.usage)
    value = schema.model_validate(response.payload)
```

逐行解释：

1. deadline/Token 已耗尽时零调用，否则最多两次且不超过模型余额。
2. 每次重试前再次估算 Token 和剩余秒数。
3. `asyncio.wait_for` 把全局剩余时间变成本次调用上限。
4. usage 无论成功与否都累计。
5. Pydantic Schema 阻止未经校验 payload 进入 Node。

验证错误允许有限修复；timeout 立即停止；连接类错误归一化为 unavailable。返回的 `StructuredCall` 携带本 Node 的增量计数/usage/errors，Reducer 再合并。去掉 `min(2, remaining)` 可能形成无界修复循环。

## `generate_report`：Citation 不交给模型创造

```python
supporting = tuple(
    evidence_by_id[item] for item in supporting_ids if item in evidence_by_id
)
citations = tuple(
    {item.citation.citation_id: item.citation for item in (*supporting, *contradicting)}.values()
)
```

模型只生成报告草稿。最终 supporting/contradicting EvidenceRef 从 State 的已验证 ID 回查，Citation 从这些对象提取并按 ID 去重。只有 `EVIDENCE_SUFFICIENT` 且有 supporting evidence 才给 `PROBABLE`，其他情况诚实输出 `INCONCLUSIVE`。

输出 `final_report` 和模型用量；下一步按高风险建议进入 review 或 END。若直接使用模型 Citation，引用正确性无法保证。

## `human_review`：真实 interrupt

```python
raw_feedback = interrupt(request.model_dump(mode="json"))
feedback = HumanFeedback.model_validate(raw_feedback)
if feedback.action is ReviewAction.ACCEPT:
    return Command(
        update={"human_feedback": feedback, "review_completed": True},
        goto="__end__",
    )
```

`interrupt` 把请求写入 checkpoint 并暂停；恢复时函数从头重放，所以 interrupt 前不能放非幂等副作用。反馈再次通过 Pydantic 校验。accept 结束；request more research 重置充分性/停止原因并 goto refine。删除 `Command.goto` 的白名单声明会削弱控制流可验证性。

## 九问总结

| 问题 | 答案 |
| --- | --- |
| 做什么 | 承载调查业务步骤和降级路径 |
| 为什么 | 每个 Node 单一职责、最小 State 更新、便于 checkpoint |
| 输入 | 初始/合并/Send scoped State、Provider/Model 返回 |
| 输出 | State 增量或 human-review Command |
| State | 上表逐节点列出；并行字段由 reducer 合并 |
| 下一节点 | Builder 普通边、纯路由函数或 Command.goto |
| Python | async、泛型 Schema、tuple comprehension、dict unpack、dataclass |
| 类比 | 有状态工作流中的 activities + policy gates |
| 修改风险 | 跳过结构校验、证据外键或预算会造成幻觉引用和失控循环 |

下一篇：[Graph Routing](08-graph-routing.md)。
