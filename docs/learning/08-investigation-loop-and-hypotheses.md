# 08 调查循环与假设

## 为什么需要循环

第一轮调查可能只得到症状, 没有足够独立来源支持根因。系统因此执行:

```text
计划 → 取证 → 假设 → 验证 → 充分性判断
                     ├─ 不足且预算允许 → refine
                     └─ 足够或预算停止 → report
```

循环不是“让模型继续想”, 而是产生新的结构化 VerificationQuery 和不重复工具步骤。

## 假设生成

`generate_hypotheses` 构造 `ModelContext`, 只放入有界 Evidence 摘要。Fake Model 生成 `HypothesesOutput`, 每个 Hypothesis 包含:

- description。
- affected services。
- supporting/contradicting Evidence IDs。
- confidence。
- verification queries。
- reasoning summary。

模型输出随后经过 Pydantic Schema。隐藏思维链不进入 State, 只保存短 reasoning summary。

## 确定性验证

`verify_hypotheses` 不信任模型提供的 Evidence 外键:

1. 删除 State 中不存在的 ID。
2. 避免同一 Evidence 同时支持和反对。
3. 统计 supporting evidence 的独立 source type。
4. 无支持证据时 confidence 上限为 0.2。
5. 只有单一来源时 confidence 上限为 0.55。
6. 至少两个来源才允许标记 supported。

删除这一步会让模型可以引用不存在的 Evidence, 或用单条日志给出高置信结论。

## 充分性判断

模型返回 `SufficiencyOutput`, 但最终 sufficient 还需要:

```python
sufficient = output.sufficient and supported and len(sources) >= 2
```

输入来自当前 State 的 verified hypotheses 和 Evidence source coverage。输出写入:

- `evidence_sufficient`
- `sufficiency_reason`
- `next_investigation_queries`
- 模型 usage/errors

下一节点由 `route_after_judge`, 不是由 `output.reason` 文本决定。

## Refine 如何避免重复

`_plan_update()` 对模型计划做三层限制:

1. 工具名必须存在于 Registry。
2. 使用实际 tool name + arguments 重算 `query_key`。
3. 过滤 completed query 和当前计划内重复 query。

step ID、query key 和 round 都由可信代码重建。修改为直接信任模型 ID 会破坏幂等和跨轮去重。

## 预算矩阵

| 预算 | 检查位置 | 耗尽结果 |
| --- | --- | --- |
| 总 deadline | parse、aggregate、model call | report |
| 工具总调用 | dispatch、routing | report |
| 最大并发 | dispatch batch | 分批继续 |
| 模型调用 | structured call、routing | fallback/report |
| 估算 Token | structured call、routing | fallback/report |
| 最大研究轮数 | judge route | report |

模型结构修复最多尝试两次。第二次尝试之前重新估算 Token, 防止“为了修 JSON”突破预算。

## 报告为什么可能 inconclusive

报告 disposition 不是模型草稿字段。可信节点根据:

- stop reason 是否为 evidence sufficient。
- 是否存在 supporting Evidence。

决定 probable 或 inconclusive。预算耗尽时即使有一个领先假设, root cause 也可能不写入最终报告, confidence 被限制。

## Human feedback 如何进入下一轮

`request_more_research` 中的 `requested_queries` 写入 `human_feedback`。Fake Model 的 follow-up 逻辑把这些意图映射为现有只读工具 Schema。

人工输入仍不能:

- 增加预算上限。
- 创建未知工具。
- 执行写操作。
- 绕过服务和时间范围校验。

下一步: [FastAPI 与异步任务](09-fastapi-and-async.md)。
