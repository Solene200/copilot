# 08 `routing.py`：确定性停止与下一节点

源码：[src/incident_copilot/graph/routing.py](../../../src/incident_copilot/graph/routing.py)

## 为什么路由必须是纯函数

路由只读取 State 并返回预声明节点名：不访问网络、不调模型、不写 State。相同输入永远得到相同目标，测试可以覆盖全部分支。

## `budget_stop_reason` 的优先级

```python
existing = state.get("stop_reason")
if existing in {
    StopReason.DEADLINE_EXCEEDED,
    StopReason.TOOL_BUDGET_EXHAUSTED,
    StopReason.MODEL_BUDGET_EXHAUSTED,
    StopReason.TOKEN_BUDGET_EXHAUSTED,
}:
    return existing
```

已经写入的硬停止原因优先保留，避免后续节点把 deadline 改写成较弱原因。

```python
if state.get("deadline_exceeded", False):
    return StopReason.DEADLINE_EXCEEDED
if state.get("tool_call_count", 0) >= state["max_tool_calls"]:
    return StopReason.TOOL_BUDGET_EXHAUSTED
if state.get("model_call_count", 0) >= state["max_model_calls"]:
    return StopReason.MODEL_BUDGET_EXHAUSTED
...
return None
```

使用 `>=` 是边界关键：达到预算即停止。输入是合并后的计数和 usage，输出是可审计枚举，不改变 State。各 Node 可以把返回值写入 `stop_reason`。

## `decide_after_judge`

```python
budget_reason = budget_stop_reason(state)
if budget_reason is not None:
    return RouteDecision(RouteTarget.REPORT, budget_reason)
if state.get("evidence_sufficient", False):
    return RouteDecision(RouteTarget.REPORT, StopReason.EVIDENCE_SUFFICIENT)
if state["research_round"] >= state["max_research_rounds"]:
    return RouteDecision(RouteTarget.REPORT, StopReason.MAX_RESEARCH_ROUNDS)
return RouteDecision(RouteTarget.REFINE, None)
```

逐行代表固定优先级：硬预算 → 证据充分 → 最大轮数 → 才能 refine。`@dataclass(frozen=True, slots=True)` 让决策不可变且字段明确。Node 使用 stop reason 写报告，conditional edge 使用 target 选择下一节点。

若先判断 evidence sufficient，刚好超时的调查可能被错误记录为“证据充分停止”；若移除最大轮数，证据持续不足时会无限循环。

## 三个路由适配器

| 函数 | 读取 | 返回 | 影响 |
| --- | --- | --- | --- |
| `route_after_parse` | deadline stop | plan/report | 过期请求跳过所有外部调用 |
| `route_after_judge` | `decide_after_judge` | refine/report | 控制调查循环 |
| `route_after_report` | remediation risk | human_review/END | 高风险建议必须审核 |

```python
if any(
    step.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    for step in report.remediation_steps
):
    return "human_review"
return "__end__"
```

生成器表达式让 `any` 遇到首个高风险步骤即可停止。输入是已构造领域报告，State 不变化。删除这一边会让高风险动作未经确认直接结束；把所有报告都送 review 会增加不必要人工阻塞。

## 九问总结

| 问题 | 答案 |
| --- | --- |
| 做什么 | 把 State 映射到有限节点名和停止原因 |
| 为什么 | 终止权由可信代码掌握，模型不可自由跳转 |
| 输入 | reducer 合并后的 InvestigationState |
| 输出 | Literal 节点名或 RouteDecision |
| State | 路由不写 State；Node 写入决定所需字段 |
| 下一节点 | 返回值经 Builder path_map 映射到真实 Node/END |
| Python | frozen dataclass、Literal、Enum、generator/any |
| 类比 | 状态机 guard condition 或 policy engine |
| 修改风险 | 优先级和边界错误会错误归因停止原因或造成无界循环 |

下一篇：[ModelProvider](09-model-provider.md)。
