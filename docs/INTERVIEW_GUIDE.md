# IncidentCopilot 面试指南

## 1. 一分钟项目介绍

IncidentCopilot 是一个证据驱动的智能故障诊断项目。我用 LangGraph 把事故解析、调查计划、多源并行取证、假设生成、充分性判断、有限追加调查、报告和人工审核建模为可恢复工作流。系统默认完全离线，使用脱敏 Fixture、Fake Model 和 Fake Embedding；作品集演示可以把 OpenTelemetry 指标经 Collector 和 Prometheus 接入同一 `MetricsProvider`，最终报告中的每个关键结论都能回指 Evidence ID 和 citation。

项目重点不是“让模型直接猜根因”，而是展示 AI 应用中的控制流、状态生命周期、工具安全、证据溯源、失败降级、离线评估和生产化边界。

## 2. 项目背景与问题定义

传统告警页面把日志、指标、Trace、发布变更和 Runbook 分散在多个系统。工程师需要在事故压力下反复切换工具，并判断哪些信号是症状、原因或噪声。IncidentCopilot 把这些查询抽象为只读工具，让 Agent 形成可验证假设，但不允许它任意执行命令或自动修复。

核心约束：

- 无付费 API Key 也能运行、测试和演示。
- 证据来源、时间、服务和引用不可丢失。
- 循环、并行和模型/工具调用必须有硬预算。
- Fixture 与真实 Adapter 走同一 Protocol。
- 失败必须成为可观察的 coverage gap，而不是被吞掉。
- 高风险建议必须由人确认。

## 3. 为什么使用 LangGraph

这个问题不是单轮问答，而是一个有状态、有分支、有并行、有循环、有暂停恢复的流程。LangGraph 提供了本项目实际使用的能力：

- `StateGraph` 显式声明节点和边，控制流可视化、可测试。
- `Send` 根据调查计划动态分发并行工具调用。
- reducer 合并并行分支返回的 Evidence、StepResult 和错误。
- conditional edge 让停止条件由代码而不是模型决定。
- checkpointer 保存 `thread_id` 对应状态。
- `interrupt()` / `Command(resume=...)` 支持 Human-in-the-loop。

如果只用普通链式调用，并行汇合、二次调查、预算终止和恢复语义会散落在业务代码中；如果用完全自治 Agent，高风险工具和不可预测循环又难以控制。LangGraph 在二者之间提供了显式状态机。

## 4. 当前 Graph 流程

```text
parse_incident
  → build_investigation_plan
  → collect_evidence (Send 并行、按 max_parallel_tools 分批)
  → aggregate_evidence
  → generate_hypotheses
  → verify_hypotheses
  → judge_evidence
      ├─ 证据不足且预算允许 → refine_investigation → collect_evidence
      └─ 足够或预算终止 → generate_report
          ├─ 高风险建议 → human_review interrupt
          └─ 无需审核/审核接受 → END
```

必须强调：源码图由 `scripts/render_graph.py` 从实际编译 Graph 生成，`docs/GRAPH_CURRENT.md` 不是理想化流程图。

## 5. State 设计

Graph State 保存：

- 事故上下文、调查计划、待执行/已完成步骤。
- 有界 `EvidenceRef`，不是无界原始 payload。
- 假设、充分性、下一轮查询。
- 研究轮次、工具/模型调用数、Token usage 和 deadline。
- 分类错误、停止原因、最终报告。

为什么用 reducer：`Send` 分支会并行返回局部更新，普通覆盖会丢数据。项目对 Evidence 和步骤使用稳定 ID 去重、确定性排序和上限裁剪，因此相同结果重放不会无限增长，测试也能复现。

需要诚实说明的缺口：当前没有独立 Evidence Store，完整 Evidence 在工具执行后被投影为 `EvidenceRef`；极端高扇出时全局 top-100 裁剪也不保证每类来源配额。

## 6. RAG 设计

写入链路：

```text
Markdown Loader → TOML metadata 校验 → heading-aware split
→ deterministic embedding → vector index + BM25
```

查询链路：

```text
query rewrite → metadata filter → BM25/vector search
→ reciprocal rank fusion → content-hash dedupe → citation-preserving hits
```

选择 Hybrid Search 的原因：Runbook 中既有精确错误码和配置名，也有自然语言描述。BM25 擅长精确词，向量检索补充语义召回，RRF 避免直接比较两个不可同尺度的原始分数。

默认 Fake Embedding 是确定性 signed-hash，只用于验证管线、排序稳定性和无网络测试，不应声称有真实语义质量。pgvector Adapter 已实现参数化 SQL 契约，但默认演示仍使用内存向量索引。

## 7. 调查循环如何终止

终止权在代码：

1. 总 deadline 到期。
2. 工具调用预算耗尽。
3. 模型调用预算耗尽。
4. 估算 Token 预算耗尽。
5. 证据充分。
6. 达到最大研究轮数。

Graph 在并行分发前预留工具预算，避免多个分支同时超额。结构化模型输出无效时只有限重试；重试前也检查 deadline 和 Token 预算。测试覆盖二次调查、最大轮数、工具/模型/Token 预算和 deadline。

## 8. 工具安全

- 七个工具全部只读，不提供 shell、SQL 写入或自动修复。
- 工具名和输入 Schema 均为 allow-list。
- 服务名、24 小时时间窗、limit、top_k、depth 和枚举字段有边界。
- `QueryContext` 传递 correlation ID、deadline 和剩余预算。
- Registry 统一执行 timeout、有限重试、Evidence source/service/time 校验和异常归一化。
- Prometheus Adapter 不接受任意 PromQL，只从领域指标映射生成模板。
- HTTP 响应有字节、序列和采样点上限；NaN、Infinity、畸形 JSON 和错误 result type 会被拒绝。
- API、事件和异常响应递归脱敏，不回传原始 Graph State 或密钥。

## 9. 真实数据源接入

当前只实现了真实 Prometheus metrics：

```text
OTel SDK demo emitter
→ OTLP/HTTP
→ OpenTelemetry Collector
→ Prometheus exporter
→ Prometheus scrape/query_range
→ PrometheusMetricsProvider
→ query_metrics Tool
→ LangGraph Evidence/Report
```

Provider 返回的 Evidence 包含 source、查询时间窗、服务、样本摘要、请求 URI、locator 和内容哈希。默认其他观测源仍为 Fixture，所以这是 mixed-source 演示。

为什么没有直接让 Graph 调 Prometheus：厂商语法和 HTTP 错误会泄漏进编排层，测试也必须依赖外部服务。通过 `MetricsProvider`，Graph 与 Registry 无需知道 PromQL，单元测试只注入 fake transport，容器验收再运行真实端点。

## 10. Evaluation

离线数据集包含三个不同根因的脱敏样例，Runner 输出逐样例 JSONL、`summary.json` 和 `summary.md`。评估项包括：

- 服务定位、故障类型。
- Retrieval Recall@K、MRR。
- 工具选择、工具参数。
- Evidence relevance、引用正确性。
- 根因词法准确率。
- 调查轮数、工具次数、wall-clock、估算 Token。

不能回避的限制：只有三个同仓样例；Fake Model 和 Fake Embedding 是确定性实现；根因 evaluator 是版本化词法规则而非人工盲审；Token 是字符估算；因此已提交的 1.0 指标只能证明 pipeline 在这三个样例上的行为，不能外推生产准确率。LangSmith 只作为显式可选 tracing，没有进入默认基线。

## 11. Checkpoint、Streaming 与 HITL

- `investigation_id` 和 `thread_id` 共享稳定 UUID；每次初始/恢复生成新的 `run_id`。
- LangGraph checkpoint 可使用内存或官方 PostgreSQL saver。
- Investigation Service 把节点、工具、Evidence、预算和报告更新投影为有序 SSE 事件。
- 高风险 remediation 在报告后进入 `interrupt()`，API 只接受严格 `HumanFeedback`。
- `request_more_research` 的结构化查询会进入下一轮 `ModelContext`；离线 Fake Model 也会把该意图转换为真实、受限的工具步骤，而不是只做一次固定模板路由。
- 重复恢复和无效反馈返回明确冲突/校验错误。

生产缺口：Investigation Repository 和 SSE 历史仍在内存，后台任务是进程内 `asyncio.Task`。PostgreSQL checkpoint 能恢复 Graph 状态，但不能代替持久化任务仓储、事件总线或分布式 worker。

## 12. 生产化不足

面试时应主动说明：

- 没有真实 LLM Adapter、Prompt 安全评测或线上成本控制。
- 没有 Loki/Tempo Adapter，官方 OpenTelemetry Demo 指标语义也未映射。
- 没有持久化 Investigation/Event Repository 和外部 Evidence Store。
- 没有鉴权、RBAC、租户隔离、审计保留策略或 secret manager 集成。
- 没有分布式任务队列、worker lease、取消与多实例竞争处理。
- PostgreSQL 未验证 HA、断线重连、压力和备份恢复。
- Evaluation 数据规模太小，没有人工盲审和线上回放。
- 未做吞吐、P95、资源占用或扩展性 benchmark。

这些不意味着架构不可用，而是明确区分“作品集级生产边界”与“已完成生产系统”。

## 13. 可用于简历的真实描述

下面的描述只包含仓库中已实现并验证的能力：

- 从零实现基于 LangGraph 的多源故障调查工作流，使用动态 `Send` 并行取证、确定性 reducer、显式循环预算与结构化输出校验，生成可回溯 Evidence ID/citation 的诊断报告。
- 设计 Provider/Tool Registry 边界，为日志、指标、Trace、变更、拓扑和知识查询统一输入校验、超时、有限重试和错误降级，并实现受限 Prometheus HTTP API Adapter。
- 构建离线 Hybrid RAG 与版本化 Evaluation 管线，支持 BM25/向量 RRF、metadata filter、去重、引用保留，以及逐样例原始结果和汇总报告。
- 实现 FastAPI 调查 API、SSE、PostgreSQL LangGraph checkpoint 和高风险建议 Human-in-the-loop，并用 Docker Compose 验证 OTLP → Collector → Prometheus → LangGraph 真实指标链路。

不要写“生产级”“自动修复”“高准确率”“支持全部 OpenTelemetry Demo”“大规模并发”或没有真实 benchmark 支持的百分比/延迟数字。

## 14. 可能的面试追问

### 为什么不用 ReAct Agent？

ReAct 适合开放探索，但事故响应需要确定停止条件、固定安全边界、可恢复状态和人工审批。这里让模型提出结构化意图，Graph 和 Registry 掌握执行权。

### 并行工具调用真的并行吗？

Graph 使用 `Send` 动态分发。测试中的 barrier provider 要求七个初始分支都到达后才释放；如果是串行执行，测试会超时。并发上限低于计划数时，Graph 分批运行而不是丢弃步骤。

### Provider 失败怎么办？

Registry 把 invalid query、timeout、unavailable、rate limit 和 malformed response 分类。可重试错误有限重试且受 deadline 约束；一个分支失败不会取消其他并行分支，最终报告记录 coverage gap。

### 如何防止 citation 幻觉？

模型只生成叙事草稿。报告构建器从 State 中存在的 EvidenceRef 附加 supporting evidence 和 citation；引用 ID 必须属于收集到的 Evidence，测试验证集合包含关系。

### 为什么 State 不存原始日志？

原始日志可能大且敏感，会放大 checkpoint、Token 和隐私风险。State 只保存稳定 ID、截断摘要、时间、服务、分数和 citation。生产版应把原始 payload 放外部 Evidence Store。

### RRF 为什么优于直接加权分数？

BM25 和向量相似度不在同一量纲，直接加权需要校准。RRF 只依赖排名，作为小型离线基线更稳定、可解释；生产中仍应基于标注集调参或增加 reranker。

### Prometheus 查询如何防注入？

用户提交的是受校验领域指标，不是 PromQL。Adapter 通过固定 mapping 和聚合 allow-list 生成模板；服务名也经过字符集限制，base URL 只来自可信配置。

### Checkpoint 能否保证任务不丢？

只能保证 Graph 执行状态可恢复。当前 Repository、SSE 历史和后台调度不持久，因此还不能保证完整任务生命周期高可用。

### Evaluation 的 1.0 指标说明什么？

只说明当前确定性 Agent 在三个版本化同仓样例上与词法标签一致，证明 evaluator 和输出管线可运行；不说明生产准确率，也不代表真实 LLM 泛化能力。

### 下一步最有价值的改进是什么？

优先持久化 Investigation/Event Repository 和 Evidence Store，然后接入一个真实 LLM/embedding 并扩展人工审阅数据集；再增加 Loki/Tempo Adapter、鉴权与多实例 worker。每一步都应保持 Fixture 契约和离线回归。
