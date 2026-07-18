# 05 `state.py`：State 与 Reducer

源码：[src/incident_copilot/graph/state.py](../../../src/incident_copilot/graph/state.py)

## State 是通道契约

`InvestigationState` 是 `TypedDict(total=False)`。它描述所有可能出现的通道，但节点可以只返回自己负责的最小更新。没有 reducer 的字段采用覆盖语义，带 `Annotated` 的字段按绑定函数合并。

```python
class InvestigationState(TypedDict, total=False):
    completed_steps: Annotated[tuple[StepResult, ...], merge_step_results]
    evidence: Annotated[tuple[EvidenceRef, ...], merge_evidence]
    tool_call_count: Annotated[int, add_count]
    model_usage: Annotated[ModelUsage, add_usage]
```

逐行理解：

1. `TypedDict` 只提供字典键的静态类型信息，不会像 Pydantic 一样运行时校验。
2. `total=False` 允许 Node 返回 `{"evidence": refs}`，不用复制完整 State。
3. `Annotated[T, reducer]` 同时告诉类型检查器值是 `T`，告诉 LangGraph 如何合并并行更新。
4. `tuple` 避免节点原地修改共享列表。

后端类比是带合并策略的事件流物化视图。删除 `Annotated` 后，多个 `collect_evidence` 分支会互相覆盖。

## `_merge_bounded_by_id` 逐行解释

```python
merged: dict[str, ItemT] = {}
for item in (*left, *right):
    item_id = identity(item)
    current = merged.get(item_id)
    if current is None or (rank(item), _canonical_model(item)) < (
        rank(current),
        _canonical_model(current),
    ):
        merged[item_id] = item
```

- `ItemT` 绑定 `BaseModel`，所以泛型元素都能稳定序列化。
- `(*left, *right)` 新建元组并遍历两个输入。
- `identity` 提供业务幂等键，例如 `evidence_id`。
- 同 ID 不同载荷时，以 rank 加规范 JSON 选择固定胜者，而不是“最后完成的分支获胜”。

```python
return tuple(
    sorted(
        merged.values(),
        key=lambda item: (rank(item), identity(item), _canonical_model(item)),
    )[:limit]
)
```

统一排序后再裁剪，保证 `merge(a, b)` 与 `merge(b, a)` 结果相同。`lambda` 是小型匿名函数。若先按每个分支裁剪再合并，全局高质量证据可能被错误丢弃；若没有 `limit`，State 会随循环无限膨胀。

## 三个集合 reducer

| Reducer | ID | 排序/上限 | 输入和输出 |
| --- | --- | --- | --- |
| `merge_evidence` | `evidence_id` | 相关度、可靠度降序，100 | 多分支 EvidenceRef 增量 → 有界证据集 |
| `merge_step_results` | `step_id` | step ID，200 | 重放/分支 StepResult → 幂等执行历史 |
| `merge_errors` | `error_id` | error ID，100 | 脱敏错误增量 → 有界错误集 |

这些函数只合并传入值，不读取网络或时钟。它们没有“下一节点”概念，但结果会被 aggregate、judge 和 report 读取。

## 计数和 Token reducer

```python
def add_count(left: int, right: int) -> int:
    return left + right
```

每个并行分支只返回增量 `1`。若分支读取旧总数再写 `old + 1`，两个分支都可能写同一个结果。

```python
return ModelUsage(
    input_tokens=left.input_tokens + right.input_tokens,
    output_tokens=left.output_tokens + right.output_tokens,
    estimated=left.estimated or right.estimated,
)
```

输入/输出 Token 分别相加；任一数据是估算值，聚合结果也必须诚实标记 estimated。把 `or` 改成 `and` 会错误声称混合结果是精确值。

## 字段按生命周期分组

| 阶段 | 主要字段 | 主要写入者 |
| --- | --- | --- |
| 初始 | `incident`、预算、时间、空集合 | `create_initial_state` |
| 计划 | `investigation_plan`、`pending_steps` | plan/refine Node |
| 并行收集 | `completed_steps`、`evidence`、工具计数、`errors` | collect Node + reducer |
| 推理 | `hypotheses`、充分性、下一查询 | hypotheses/verify/judge |
| 终止 | `stop_reason`、`final_report` | aggregate/judge/report |
| 审核 | `human_feedback`、`review_completed` | human_review |

## 九问总结

| 问题 | 答案 |
| --- | --- |
| 做什么 | 定义 Graph 通道和并行合并语义 |
| 为什么 | 并行完成顺序和 checkpoint 重放不应改变结果 |
| 输入 | Node 返回的局部 State 更新 |
| 输出 | 下一 superstep 可见的合并 State |
| State | reducer 是 State 真正发生合并的位置 |
| 下一节点 | Builder 的边决定；routing 读取合并后的 State |
| Python | TypedDict、Annotated、TypeVar、Callable、lambda |
| 类比 | Event sourcing 中可交换、幂等的聚合器 |
| 修改风险 | 非确定 reducer 会产生难复现并发 bug；无上限会膨胀 checkpoint |

下一篇：[Graph Builder](06-graph-builder.md)。
