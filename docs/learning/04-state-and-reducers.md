# 04 IncidentState 与 Reducer

## State 的角色

`InvestigationState` 位于 `graph/state.py`, 是 LangGraph 节点之间共享的通道定义。它使用 `TypedDict(total=False)`, 允许节点只返回最小字段增量。

```python
class InvestigationState(TypedDict, total=False):
    incident: IncidentContext
    completed_steps: Annotated[tuple[StepResult, ...], merge_step_results]
    evidence: Annotated[tuple[EvidenceRef, ...], merge_evidence]
    tool_call_count: Annotated[int, add_count]
    ...
```

这里有两类更新语义:

- 普通字段: 新值覆盖旧值。
- `Annotated[T, reducer]`: LangGraph 用 reducer 合并多个更新。

如果把 `evidence` 的 reducer 删除, 多个并行 `collect_evidence` 分支会互相覆盖, 最终通常只剩一个分支的结果。

## 字段分组

### 输入和计划

| 字段 | 类型 | 主要写入者 | 主要读取者 |
| --- | --- | --- | --- |
| `incident` | `IncidentContext` | `create_initial_state` | 几乎所有节点 |
| `investigation_plan` | `InvestigationPlan` | plan/refine | dispatch、事件投影 |
| `pending_steps` | tuple | plan/refine | dispatch |
| `current_step` | `InvestigationStep` | `Send` scoped state | collect |
| `completed_steps` | reducer tuple | collect | dispatch、Evaluation |

`current_step` 只存在于单个 `Send` 分支的最小 State 中。它不是所有并行步骤共享的全局游标。

### 证据和假设

| 字段 | 更新方式 | 说明 |
| --- | --- | --- |
| `evidence` | `merge_evidence` | 按 Evidence ID 去重, 全局 top 100 |
| `hypotheses` | 覆盖 | 每轮保存当前校验后版本 |
| `evidence_sufficient` | 覆盖 | judge 的结构化结果与确定性规则共同决定 |
| `sufficiency_reason` | 覆盖 | 当前充分性解释 |
| `next_investigation_queries` | 覆盖 | 下一轮结构化查询意图 |

State 保存 `EvidenceRef`, 不保存 `Evidence.content`。这是为了控制 checkpoint 和模型上下文大小。

### 预算和停止

| 字段 | 意义 |
| --- | --- |
| `research_round` / `max_research_rounds` | 当前轮与最大轮数 |
| `tool_call_count` / `max_tool_calls` | 工具实际尝试与上限 |
| `max_parallel_tools` | 单批最大并发分支 |
| `model_call_count` / `max_model_calls` | 模型调用与上限 |
| `model_usage` / `max_estimated_tokens` | Token usage 与预算 |
| `deadline_at` / `deadline_exceeded` | 调查总时间边界 |
| `stop_reason` | 最终停止原因 |

计数字段的节点输出是“本节点增量”, 不是累计总数。例如每个 collect 分支返回 `tool_call_count=1`, `add_count` 才负责合并。

### 输出、错误和审核

- `errors`: `merge_errors` 去重并限制为 100。
- `final_report`: `generate_report` 覆盖写入。
- `human_feedback`: `human_review` 恢复后写入。
- `review_completed`: 接受审核时为 true。

## Reducer 为什么必须确定性

```python
def add_count(left: int, right: int) -> int:
    return left + right
```

并行分支 A、B 无论谁先完成, `1 + 1` 都是 2。若节点读取旧总数并返回 `old + 1`, 两个分支可能都读到 0 并各写 1, 造成丢失更新。

集合 reducer 更复杂:

```text
left + right
→ 按稳定 ID 建表
→ 同 ID 冲突使用 rank + canonical JSON 决定胜者
→ 稳定排序
→ 上限裁剪
```

这让 reducer 具备以下性质:

- 幂等: `merge(x, x) == x`。
- 交换等价: `merge(a, b) == merge(b, a)`。
- 结合等价: 分批合并与一次合并一致。

相关测试位于 `tests/unit/graph/test_reducers.py`。

## 初始 State

`create_initial_state()` 是预算字段的可信写入者。它接收经过领域校验的 Incident 和 `InvestigationOptions`, 计算:

```python
started_at = clock()
deadline_at = started_at + timedelta(seconds=policy.timeout_seconds)
```

模型不会得到修改最大轮数或 deadline 的权限。删除这个集中初始化会让不同入口产生不一致默认值。

## State 与任务状态不是一回事

```text
InvestigationState
  LangGraph 节点数据和 checkpoint

InvestigationRecord
  API 任务资源和 pending/running/waiting_review 等状态
```

后端工程类比:

- `InvestigationState` 类似工作流引擎实例变量。
- `InvestigationRecord` 类似业务任务表。
- `InvestigationEvent` 类似面向客户端的事件日志。

下一步: [Graph、Node 与调查循环](05-graph-and-nodes.md)。
