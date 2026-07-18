# IncidentCopilot 路线图与阶段验收

## 1. 使用规则

- 一次只执行用户明确指定的一个 Phase。
- 状态只使用 `not_started`、`in_progress`、`completed`、`blocked`；完成必须有真实检查证据。
- 每阶段从可运行的上一阶段开始，以可运行、可测试的仓库结束。
- 后一阶段不得为了方便提前污染前一阶段的范围。
- 质量指标与性能数字只有运行 Evaluation 后才能填写，不允许用目标值冒充结果。

## 2. 总览

| Phase | 名称 | 状态 | 核心演示增量 |
| --- | --- | --- | --- |
| 0 | 需求、架构和规范 | completed | 可评审的产品/架构/Graph/数据模型蓝图 |
| 1 | 工程骨架和领域模型 | completed | FastAPI `/health` + 领域模型测试 |
| 2 | Fixture Provider 和工具层 | completed | 七类本地工具返回结构化证据 |
| 3 | RAG Indexing 和 Retrieval | completed | 可复现 Hybrid Search 与引用 |
| 4 | LangGraph 调查工作流 | completed | 完整有界调查循环与报告 |
| 5 | API、Streaming、Checkpoint、HITL | completed | 可创建、观察、恢复和审核调查 |
| 6 | Evaluation 和 Agent 可观测性 | completed | 可复现质量/成本/时延报告 |
| 7 | 真实数据源和演示 | not_started | 真实 Adapter + Docker 演示 + 面试材料 |

## 3. Phase 0：需求、架构和项目规范

### 目标与范围

只创建规划文档，不实现业务代码、依赖文件、容器、fixture 或测试代码。

### 产出

- `AGENTS.md`
- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/GRAPH_DESIGN.md`
- `docs/DATA_MODEL.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- 目录结构规划、技术取舍、Mermaid 架构图和 Graph 图

### 验收标准

- [x] 产品目标、用户、功能、非功能、范围外事项和成功定义清晰。
- [x] 架构组件职责、端口、数据流、失败策略、安全和可观测性清晰。
- [x] Graph 覆盖并行、reducer、研究循环、预算、checkpoint、HITL 和 streaming。
- [x] State 区分覆盖、累积和不可暴露给 LLM 的字段，并说明大小控制。
- [x] 四个核心领域模型、引用、计划、错误和知识 Chunk 有字段与不变量。
- [x] 至少有一张总体架构 Mermaid 图和一张 Graph Mermaid 图。
- [x] 每个后续 Phase 都有输入、产出、测试和完成定义。
- [x] 不存在 Phase 1 业务代码或伪造的测试结果。
- [x] 文档结构、相对链接、Mermaid fence 和敏感信息扫描通过。

### 实际检查

详见 [`PROGRESS.md`](PROGRESS.md) 的 Phase 0 记录。

## 4. Phase 1：工程骨架和领域模型

### 输入条件

- 用户确认 Phase 0。
- 可安装或可调用 `uv`；若需联网下载依赖，获得相应权限。
- 技术版本范围重新核验，尤其是 LangGraph/LangChain/Pydantic 兼容关系。

### 产出

- `pyproject.toml`、由 uv 生成的 `uv.lock`、`.env.example`、基础 README/Makefile。
- `src` layout、FastAPI app factory、`/health`。
- Pydantic Settings、JSON 结构化日志、异常层次与 API 错误 Schema。
- Incident/Evidence/Hypothesis/Report 的阶段内领域模型。
- Fixture 目录规范与最小无敏感信息样例结构。
- Ruff、mypy、pytest、pytest-asyncio 配置和基础单测。

### 关键测试

- 配置默认值/环境覆盖/秘密不回显。
- 所有时间必须带时区，非法时间窗口拒绝。
- 分数、枚举、证据引用与假设集合不变量。
- `/health` 正常返回；异常到 API 错误映射。
- 应用导入不需要 API Key 或外部服务。

### 完成定义

- `uv lock --check`、Ruff format/check、mypy 和 Phase 1 pytest 全部通过。
- `uv run uvicorn ...` 可启动，`/health` 有真实请求验证。
- README 记录本地命令；PROGRESS 和本路线图更新真实结果。

### 实际验收

- [x] `uv sync` 和 `uv lock --check` 成功，锁文件解析 37 个包。
- [x] Ruff format/check 全部通过。
- [x] `mypy src` 与 `mypy src tests` 全部通过。
- [x] 严格审查并补齐领域不变量、引用解析、统计一致性和脱敏边界后，全量测试为 43 passed、0 failed、0 warning。
- [x] 独立 Uvicorn 进程启动后，真实 HTTP `/health` 请求返回 200 和预期 JSON。
- [x] 默认导入、启动与测试不需要 API Key、Docker、数据库或网络服务。
- [x] 未实现 Provider、RAG、LangGraph、持久化或其它 Phase 2+ 功能。

## 5. Phase 2：Fixture Provider 和工具层

### 输入条件

- Phase 1 通过；领域 Evidence 与异常契约稳定。

### 产出

- Log/Metrics/Trace/Change/Topology/Knowledge Provider Protocol。
- Fixture Provider、Tool Registry、结构化输入/输出、统一执行包装器。
- 七个工具：日志、指标、Trace、拓扑、近期变更、Runbook、类似事故。
- payment-service 基准事件的日志、指标、Trace、变更、拓扑 fixture。
- 超时、重试、参数边界、错误转换和结构化遥测。

### 关键测试

- 每个 Provider 的正常、空结果、过滤、limit 和引用。
- 非法服务/时间/查询窗口拒绝。
- 超时与临时故障有限重试；永久错误不重试。
- Registry 重名、未知工具、预算和统计。
- 一个 Provider 失败不影响直接调用其它 Provider。

### 完成定义

- 七类工具均有独立单测与固定输出快照/结构断言。
- 默认测试无网络；Ruff、mypy、相关 pytest 通过。
- fixture ground truth 与噪声设计在文档中可审阅。

### 实际验收

- [x] 六个 Provider Protocol 与七个工具均通过统一 Registry 装配，FixtureProvider 同时满足全部端口。
- [x] 七工具逐项测试固定 evidence ID，并验证来源、时间、服务和 citation；空结果与 limit 保持确定性。
- [x] Registry 覆盖重名/未知工具、严格参数、预算、deadline、有限超时重试、临时/永久 Provider 失败、错误归一化，以及返回证据的 service/time/limit 请求边界。
- [x] payment-service fixture 含 12 条脱敏证据、真实规范化 content SHA-256、支持/反证/噪声和隔离的 ground truth。
- [x] `uv lock --check`、Ruff format/check、`mypy src tests`、27 项 Phase 2 测试与 99 项全量测试通过（含 Phase 2/3 严格审查加固测试）。
- [x] 默认测试未访问网络、付费 API、数据库或真实可观测平台；未实现 RAG、LangGraph 或下一阶段占位业务代码。

## 6. Phase 3：RAG Indexing 和 Retrieval

### 输入条件

- Phase 2 通过；Knowledge Retriever 契约可实现。

### 产出

- 文档加载、规范化、切分、metadata、内容 hash 与幂等 ingest。
- 确定性 Fake Embedding、内存 VectorStore、pgvector Adapter。
- BM25、向量检索、RRF Hybrid Search、去重、metadata filter、可选 reranker。
- Query rewrite、上下文压缩与 citation 保留。
- 少量 Runbook、服务说明和历史事故文档。

### 关键测试

- 切分边界、metadata 继承、幂等重建和 embedding 确定性。
- BM25/向量/混合排序、过滤、去重、top_k 和引用解析。
- pgvector Adapter 使用受控数据库集成测试；默认单测不要求数据库。
- 小型固定数据集计算 Recall@K、MRR；逐样本结果落盘/输出，不虚构分数。

### 完成定义

- 同一 fixture 多次 ingest/search 结果稳定。
- 每个返回 Chunk 有可解析 Citation。
- 无在线 Embedding API 时所有必需测试通过。

### 实际验收

- [x] 4 个 TOML-frontmatter Markdown 文档覆盖 Runbook、服务说明和历史事故；加载后形成 12 个标题感知 Chunk。
- [x] 文档/Chunk 使用规范化 SHA-256 与稳定 ID；重复 ingest 后仍为 4 documents / 12 chunks，检索结果一致。
- [x] Fake Embedding、BM25、内存 VectorStore、RRF Hybrid Search、content-hash 去重、metadata filter、top_k、citation 和规则 Query Rewrite 均有固定测试。
- [x] `RagKnowledgeProvider` 在不修改 Phase 2 工具名/Schema 的情况下接入 `search_runbooks` 与 `search_similar_incidents`。
- [x] `PgVectorStore` 提供参数化 upsert/delete/search、事务式文档替换及维度/表名/embedding 版本边界；Adapter 不执行运行时 DDL，schema 由 Alembic migration 管理。recording session contract 测试通过；当前环境无 PostgreSQL/pgvector，未把真实数据库集成标为通过。
- [x] 3 条手写 fixture 回归查询实际得到 Recall@3 `1.0`、MRR `7/9`；仅作为这 3 条固定样例的回归值，不代表泛化质量。
- [x] 初始化与检索脚本真实运行成功，分别报告 4 documents / 12 chunks 和带原始 citation 的检索结果。
- [x] `uv lock --check`、Ruff format/check、`mypy src tests`、29 项 Phase 3 测试与 99 项全量测试通过；默认路径未访问网络或在线模型/embedding。严格审查补齐 CJK chunk 上限、文件预读上限、同步检索的事件循环隔离、失败 ingest 状态保留及 embedding 版本隔离。

## 7. Phase 4：LangGraph 调查工作流

### 输入条件

- Phase 2 工具与 Phase 3 Retriever 稳定；Fake Model 契约明确。

### 产出

- `IncidentState`、自定义 reducer、Graph builder、路由纯函数与节点。
- 动态并行证据收集、聚合、假设生成/验证、充分性判断和 refine 循环。
- 研究轮数、工具、Token、deadline 与并发预算。
- 最终报告、失败降级和 Graph Mermaid 可视化。
- 可复现 Fake Model；真实模型只作为可选 Provider。

### 关键测试

- [`GRAPH_DESIGN.md`](GRAPH_DESIGN.md) 第 9 节的完整矩阵。
- 节点单测、路由真值表、reducer 结合/交换/幂等属性或等价用例。
- 正常和失败图路径的端到端固定断言。

### 完成定义

- Fixture 基准事故生成结构化、带引用报告。
- 循环在所有预算边界精确停止，无路径可无限执行。
- 部分 Provider 失败仍产出诚实的受限报告。
- 图测试、静态检查通过，生成图与文档一致。

### 实际验收

- [x] LangGraph 1.2.9 的 `StateGraph` 使用动态 `Send` 将最小作用域工具步骤分发到同一 `collect_evidence` 节点，并通过 reducer 汇合；并发上限小于计划长度时会经过 aggregate 回边继续分批执行。异步栅栏测试证明 7 个初始分支同时启动，不以耗时阈值冒充并行证据。
- [x] Evidence、StepResult 和 Error reducer 使用稳定 ID 去重及确定性有界排序；同 ID 冲突载荷也与合并顺序无关，并行计数使用增量求和；单测覆盖幂等、交换/结合等价用例和路由优先级。
- [x] 调查循环具有最大研究轮数、真实工具尝试、并发、模型调用、估算 Token 和 deadline 预算；二次调查执行 10 个不重复查询，最大轮数精确停在第 2 轮，已过期 invocation 为 0 工具/0 外部模型调用，工具/模型/Token 预算均有终止断言。
- [x] 所有模型任务通过 Pydantic 结构化 Schema；模型 timeout、Provider 异常或连续无效输出每任务最多尝试 2 次，之后使用可审计的规则/Fake 降级，不调用在线模型或付费 API。step/query identity 和 round 由可信代码重算。
- [x] 单 Change Provider 失败时其它 6 个初始分支成功，错误进入 State 和报告 limitation，仍生成带真实 Evidence ID 与 Citation 的报告。
- [x] `scripts/run_investigation.py` 实际生成 `probable` 报告：1 个研究轮、7 次工具调用、4 次 Fake Model 调用、13 条六类证据，停止原因 `evidence_sufficient`；这些只是固定演示运行数据，不是性能或准确率评估。
- [x] [`GRAPH_CURRENT.md`](GRAPH_CURRENT.md) 由当前编译图导出，`scripts/render_graph.py --check` 与集成测试逐字符校验；图中没有未实现的 HITL/checkpoint/API。
- [x] `uv sync`、`uv lock --check`、Ruff format/check、`mypy src tests scripts`、47 项 Phase 4/工具预算相关测试和 130 项全量测试全部通过；默认测试无网络、无 API Key、无真实数据库。

## 8. Phase 5：API、Streaming、Checkpoint 和 HITL

### 输入条件

- Phase 4 图可通过编程接口启动并确定性完成。

### 产出

- 创建调查、查询状态、获取报告和提交审核 API。
- 后台调查生命周期与幂等请求策略。
- 版本化 SSE 事件、thread/run ID、重连语义。
- 内存测试 checkpointer + PostgreSQL 目标 checkpointer。
- `interrupt` / `Command(resume=...)` 的接受与追加调查流程。

### 关键测试

- API 校验、404/409/422/500 错误契约与幂等创建。
- SSE 顺序、终止事件、心跳/断连和敏感字段过滤。
- checkpoint 中断后跨 app 实例恢复（数据库集成环境）。
- 人审接受、追加调查、无预算时拒绝追加。

### 完成定义

- 真实 HTTP 客户端可完成创建→流式观察→审核→报告全链路。
- 进程重建后用 thread ID 恢复的集成测试通过。
- API 不泄露原始 State、秘密或未脱敏证据。

### 实际验收

- [x] 提供 `POST /api/v1/investigations`、`GET /api/v1/investigations/{id}`、`GET /api/v1/investigations/{id}/events`、`POST /api/v1/investigations/{id}/resume`；创建使用后台任务和可选 `Idempotency-Key`，相同请求重放、不同载荷冲突均有 HTTP 测试。
- [x] `investigation_id` 与 `thread_id` 共享稳定 UUID，每次初始/恢复执行使用不同 `run_id`；Graph 通过 checkpointer 和 `thread_id` 暂停/恢复。新 Graph 与新任务仓储可从同一 saver 重建暂停任务并完成恢复。
- [x] 报告首个生产变更建议标为 high risk，源码条件路由进入 `human_review` 并调用 `interrupt()`；`Command(resume=...)` 只接受 Pydantic `accept` / `request_more_research`。测试覆盖接受、追加调查后二次暂停、无预算拒绝、非法反馈和重复恢复 409。
- [x] SSE 使用版本化事件、单调 sequence/event ID、`Last-Event-ID` 重放、heartbeat、断连停止与静默点关闭；事件只映射安全节点/工具/Evidence/Citation 摘要，状态和事件测试验证敏感原始 query 不回显。
- [x] 默认 `InMemorySaver` 零网络运行；可选依赖锁定官方 `langgraph-checkpoint-postgres` 3.1.0，PostgreSQL backend 在 FastAPI lifespan 内持有 `AsyncPostgresSaver` 并先执行 `setup()`，缺少 DSN/extra 时显式配置失败。
- [x] 本地真实 TCP 演示完成创建→50 个 SSE 事件→高风险暂停→审核接受→报告，初始与恢复 `run_id` 不同；该次数只描述固定 fixture 演示，不是性能或质量评估。
- [x] `uv sync`、`uv lock --check`、Ruff、`mypy src tests scripts`、18 项 Phase 5 定向测试、148 项全量测试和源码 Mermaid 一致性检查通过；默认测试没有调用在线模型、在线 embedding、付费 API 或数据库。
- [x] 环境加固后确认 BIOS 虚拟化、Windows Hypervisor、WSL2 与 Docker Linux Engine 正常；Docker PostgreSQL 18.4 / pgvector 0.8.5 上完成真实跨应用进程恢复：第一个进程暂停，第二个进程以同一 thread 恢复并接受审核完成。数据库实际写入 11 条 checkpoint 和 102 条 checkpoint write。

## 9. Phase 6：Evaluation 和 Agent 可观测性

### 输入条件

- Phase 5 有稳定调用与事件接口；报告 Schema 定版。

### 产出

- 版本化小型离线事故数据集和 ground truth。
- Evaluation Runner 与逐样本 JSON/表格结果。
- 服务定位、故障类型、Recall@K、MRR、Evidence Relevance、Citation Correctness。
- Tool 选择/参数、根因诊断、平均轮数、调用数、端到端时延和 Token/估算成本。
- 节点/工具/模型 OpenTelemetry spans；可选 LangSmith exporter。
- `docs/EVALUATION.md`。

### 关键测试

- 每个 evaluator 用手算样例验证边界、空集合和分母。
- Runner 失败样本不丢失，聚合结果可追溯到逐样本。
- Fake Token 与真实 Token 标记区分；成本缺定价时为 unavailable。
- Telemetry 关闭时零外部依赖且业务不变。

### 完成定义

- 一条命令在离线模式生成真实评估报告。
- 文档报告实际数值、数据集大小、置信限制和失败样本。
- 不把 fixture 测试通过率包装成泛化准确率。

### 实际验收

- [x] 版本化 `1.0.0` 数据集包含 3 个不同根因的脱敏 Fixture；ground truth 只进入 evaluator，不传入 Graph。
- [x] 一条 `uv run python -m scripts.evaluate_offline` 命令生成逐样例 JSONL、JSON 汇总和 Markdown 汇总；失败样例保留且汇总计数可追溯。
- [x] 覆盖服务定位、故障类型、Recall@K、MRR、工具选择/参数、Evidence relevance、引用正确性、根因准确率、轮数、工具次数、wall-clock 时延和 Token；Fake Token 标记 estimated，缺定价时成本为 unavailable。
- [x] 节点、工具、结构化模型调用具有默认关闭的 OpenTelemetry spans；`observability` extra 为可选 Apache-2.0 依赖。LangSmith 必须显式开启，默认 tracing context 即使外部环境变量为 true 也保持离线。
- [x] Phase 6 基线真实运行 3/3 完成、0 失败；完整数值、逐样例原始结果和限制记录在 `docs/EVALUATION.md` 与 `artifacts/evaluation/phase6-baseline/`，未声明泛化准确率或性能 benchmark。
- [x] `uv sync`、`uv lock --check`、Ruff format/check、`mypy src tests scripts`、17 项 Phase 6 定向测试和 165 项全量测试通过；默认测试拒绝网络连接且没有调用付费 API。

## 10. Phase 7：真实数据源和演示

### 状态

`completed`（2026-07-18）

实现范围：Prometheus HTTP API 是当前唯一真实观测 Adapter；OpenTelemetry demo emitter 经 OTLP/HTTP、Collector 和 Prometheus 把 synthetic incident metrics 送入 LangGraph。日志、Trace、变更和拓扑仍为 Fixture。完整验收记录见 [`PROGRESS.md`](PROGRESS.md)，演示与面试材料见 [`DEMO_GUIDE.md`](DEMO_GUIDE.md) 和 [`INTERVIEW_GUIDE.md`](INTERVIEW_GUIDE.md)。

### 输入条件

- Phase 6 有可比较基线；本机可运行容器或明确的替代环境。

### 产出

- 至少一个 Prometheus/Loki/Tempo/OpenTelemetry Demo 真实 Adapter。
- Docker Compose、健康检查、迁移/seed/ingest/demo/evaluation 脚本。
- 可选 Streamlit 页面，不得成为核心依赖。
- 完整 README、架构与演示场景、`docs/INTERVIEW_GUIDE.md`。
- 简历项目描述、面试问题与参考答案。

### 关键测试

- Provider contract tests 同时运行 fixture 与真实 Adapter。
- Compose 冷启动、健康检查、seed、调查和清理说明验证。
- Demo 命令在全新环境按 README 实际执行。
- 真实 Adapter 不可用时 fixture 路径仍完整。

### 完成定义

- 一条受支持命令启动依赖，一条命令运行基准演示。
- README 的所有快速开始步骤在干净环境验证。
- 面试材料能解释设计取舍、失败模式与真实评估结果。
- 所有必需检查通过，已知限制显式记录。

## 11. 跨阶段质量门禁

Phase 1 起每阶段至少运行：

```text
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest <本阶段相关目录>
```

阶段报告必须区分：通过、失败、因当前范围不适用、因环境阻塞未运行。后两者不能写成通过。
