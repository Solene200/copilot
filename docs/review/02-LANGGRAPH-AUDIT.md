# LangGraph 专项审查

## 结论

LangGraph 是本项目真正的控制平面，而非为了简历贴标签。`build_investigation_graph()` 将 10 个节点、固定边、条件路由、`Send` fan-out、checkpoint、`interrupt` 与 `Command` 组合成可恢复工作流。Graph 承担编排、状态生命周期与停止决策；文件、HTTP 和 fixture 读取留在 Provider，符合仓库架构边界。

设计总体评价为 8.5/10。主要缺陷不在图结构，而在传入图的默认智能与预算细节：模型计划强场景偏置，工具 Registry 的重试能力被节点上下文关闭，模型上下文只覆盖首个服务。

## 实际拓扑

节点定义于 `graph/nodes.py:GraphNodes`，编译位于 `graph/builder.py:build_investigation_graph()`：

1. `parse_incident`
2. `build_investigation_plan`
3. `collect_evidence`
4. `aggregate_evidence`
5. `generate_hypotheses`
6. `verify_hypotheses`
7. `judge_evidence`
8. `refine_investigation`
9. `generate_report`
10. `human_review`（配置启用时）

固定边保持主线清楚：START → parse；collect → aggregate；hypotheses → verify → judge；无 HITL 时 report → END。条件路由分别处理 parse 后失败、计划批量分派、聚合后下一步、证据判定、研究细化分派和报告后是否审核。

`scripts/render_graph.py --check docs/GRAPH_CURRENT.md` 实际通过，说明当前图文档与编译拓扑一致。

## State 与 reducer

`graph/state.py:InvestigationState` 把不可变输入、运行预算、计划、step 结果、EvidenceRef、假设、错误、使用量、报告与审核状态放在一个 TypedDict 中。关键判断：

- Evidence、steps、errors 使用按稳定 ID 合并的 reducer，适合 `Send` 并行更新，避免完成顺序改变最终 state。
- 计数器和 usage 使用加法 reducer，能汇总并行分支。
- 大原始 payload 不进入 state，只有截断摘要和有界集合；这是正确的 checkpoint 成本控制。
- 上限（evidence 100、steps 200、errors 100）防止无限增长。
- state 字段较多但均对应跨节点生命周期；当前不属于无意义“大状态对象”。

需要修正的一点是调用计数语义。`collect_evidence()` 无论 `ToolExecutionResult.attempts` 为多少，都把 `tool_call_count` 增加 1。当前 Graph 又固定 `remaining_tool_calls=1`，恰好掩盖了该偏差；恢复真实 retry 后必须明确预算按逻辑 step 还是物理 attempt 计数，并在 stats 与停止规则中统一。

## `Send` 并行语义

`graph/builder.py:_dispatch_batch()` 为计划中的每一步创建 `Send("collect_evidence", ...)`。所有分支完成后，由 LangGraph barrier 合并进 `aggregate_evidence`。这不是伪并行循环：

- 分支只携带当前 step 与共享只读 state；
- reducer 按 ID 合并，完成顺序不影响结果；
- `aggregate_evidence` 是唯一进入假设阶段的入口；
- 测试覆盖不同延迟下的 barrier 与确定性结果。

对当前每轮最多七个工具的规模，节点级 fan-out 合理。没有必要再拆 subgraph；那会增加 checkpoint 命名和调试成本而不改善当前业务。

## 条件路由与研究循环

`graph/routing.py` 将状态转换规则集中化，避免 Node 内直接拼接边。`judge_evidence()` 的充分条件同时要求：模型判断充分、leading hypothesis 处于 supported、至少两个独立来源。否则进入 refine 或按预算生成 inconclusive 报告。

研究循环有轮次、工具调用、模型调用和 deadline 四重边界，且 resume “继续研究”会更新 deadline 但不会重置累计预算。停止条件可解释、可测试，不存在无界 agent loop。

一个可改进点是“leading hypothesis = 序列第一项”。真实模型未来返回多个假设时，应使用显式 rank/score，而不是依赖输出顺序；默认 fake 只有一个假设，所以当前影响有限，归为 P2。

## `Command`、`interrupt` 与 checkpoint

`GraphNodes.human_review()` 调用 `interrupt(review_request)` 保存暂停点；恢复后把外部输入校验为 `HumanFeedback`，再返回 `Command`：接受/拒绝去 END，要求更多研究则去 refine。实跑证明：

- 高风险 remediation 会暂停；
- resume 使用原 thread ID；
- resume 生成新 run ID；
- 最终状态从 awaiting_review 转为 completed；
- SSE 事件贯穿前后两个 run。

默认 `InMemorySaver` 足以离线演示；`AsyncPostgresSaver` 装配代码存在，但本次未连接真实数据库，因此只能标为无法验证，而不能继承历史文档的“已验证”结论。

## LangGraph 问题

### IC-P1-02：运行链实际不重试工具

- 位置：`graph/nodes.py:GraphNodes.collect_evidence()` 构造 `QueryContext(remaining_tool_calls=1)`；`tools/registry.py:ToolRegistry.execute()` 使用 `min(max_retries + 1, remaining_tool_calls)`。
- 实际结果：最大 attempt 永远是 1，`failure.retryable` 和指数 backoff 在 Graph 路径不会触发。
- 为什么测试没发现：Registry 单元测试传入大于 1 的 remaining budget；Graph 集成测试没有注入“第一次 retryable、第二次成功”的 Provider。
- 修复方向：把剩余全局工具预算传入；按每次物理 attempt 原子扣减；并发 fan-out 前预留上限，避免多个分支各自认为拥有完整余额。
- 验收：Graph 集成测试观察到 retryable failure 后成功、attempts=2、统计与预算一致；non-retryable 仍只调用一次。

### IC-P2-01：多假设顺序被当成排名

- 位置：`GraphNodes.generate_report()` 使用第一个 hypothesis 作为 leading。
- 影响：真实 Provider 接入后，输出顺序变化可能改变 root cause。
- 修复方向：Hypothesis 增加明确 rank/综合分，或在节点内按 status、confidence、独立来源数确定性排序。
- 验收：交换模型返回顺序不改变报告根因。

### IC-P2-02：Graph phase 命名滞后

- 位置：编译 graph name 仍使用历史 phase 标签，仓库文档已声称 Phase 7。
- 影响：trace、截图和面试讲解出现版本歧义。
- 修复方向：使用稳定业务名和独立版本字段，不把开发阶段编码进运行时 graph 名。

## 是否过度设计

LangGraph 部分没有严重过度设计。`Send`、条件路由、checkpoint 与 HITL 都解决了实际需求，且有测试证明。最应避免的是继续增加 supervisor、多 agent、嵌套 subgraph 或动态工具规划框架；当前收益最高的是修正计划质量、重试预算和证据真实性。
