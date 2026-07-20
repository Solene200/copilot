# IncidentCopilot 最终独立审查：执行摘要

审查日期：2026-07-20（Asia/Shanghai）

审查基线：`main` / `7289e28fcee7f7dde017dab69c3de257570f0274`

审查方式：只读源码、配置、fixture、测试与文档；实际运行质量门禁、离线调查、FastAPI/SSE/HITL、RAG 与离线评估。除本目录报告外未修改业务代码、测试或原文档。

## 最终结论

IncidentCopilot 不是 PPT 项目。它已经形成一条可在无付费 API Key 条件下稳定运行的 LangGraph 调查闭环：七类工具并行取证、证据聚合、假设生成与验证、研究循环、结构化报告、SSE 事件、人工审核与同线程恢复均有真实实现和测试。工程分层总体清楚，196 个测试全部通过，默认支付故障 Demo 可复现。

但当前版本不应以“最终完成、具备强证据完整性、可泛化到多故障场景”的口径写入简历。最严重问题是证据哈希契约被 fixture 占位值破坏，而评估的 citation correctness 只验证对象间自洽，仍给出 1.0；其次，默认 `FakeModelProvider` 的计划固定为 payment/database-pool 查询，checkout 与 inventory 样本并没有得到场景化计划。工具重试在注册表中存在，但 Graph 调用路径把剩余调用数固定为 1，因而运行时从不重试。

严格判断如下：

- 本地可运行、可测试、可演示：是。
- 核心 LangGraph 控制流是否真实：是。
- RAG 是否为真实混合检索：是，但仅确定性本地实现，缺少 rerank、压缩和真实 pgvector 验证。
- 引用是否已证明可回溯并校验原始证据：否；定位字段存在，内容哈希未被真实验证。
- 离线评估是否可用：可作为同仓库回归测试，不足以证明 AI 泛化能力。
- 是否存在全局性严重过度设计：否；主要浪费集中在重复生成的大型学习文档与未接入真实运行链路的 pgvector 适配器。
- 是否达到“最终版本/可直接写入简历”的标准：否。先修复 P0 和前三项 P1，再以限定口径展示。

## 评分

| 维度 | 分数（10 分制） | 结论 |
|---|---:|---|
| 业务完整性 | 7.0 | 支付场景闭环完整；自然语言解析、多服务调查、反证/淘汰链不完整 |
| AI 系统复杂度 | 7.0 | 有结构化模型边界、预算与研究循环；默认模型仍是规则化 fake |
| LangGraph 设计 | 8.5 | `Send`、条件路由、`Command`、`interrupt`、checkpoint 都是实用控制流 |
| RAG 设计 | 6.5 | BM25 + 向量 + RRF、过滤、增量写入真实；缺 rerank/压缩/真实数据库验证 |
| 后端工程质量 | 7.5 | 分层、Pydantic、错误链、SSE 和并发约束较好；持久化、鉴权仍是本地阶段 |
| 测试质量 | 8.5 | 196 个高价值测试全绿；关键真实性断言缺失 |
| 评估可信度 | 5.5 | 流水线透明可复现，但样本仅 3 个、同仓库、规划偏置且 citation 指标口径过弱 |
| 本地可运行性 | 9.0 | 锁文件、质量门禁、CLI 与 API Demo 均成功 |
| 可解释性 | 8.0 | EvidenceRef、假设、事件与报告链清楚；缺原始 Evidence resolver |
| 面试价值 | 7.5 | 很适合讲 LangGraph/后端边界；不能把回归指标包装成真实 AI 效果 |
| 综合 | **7.4** | **扎实的作品集工程原型，尚非最终可信版本** |

## 最高优先级问题

| ID | 级别 | 摘要 |
|---|---|---|
| IC-P0-01 | P0 | checkout/inventory/example fixture 使用不匹配的 `content_hash`，评估仍报告 citation correctness 1.0，证据完整性与评估口径同时失真 |
| IC-P1-01 | P1 | `FakeModelProvider._plan()` 对所有服务生成 payment/database-pool 固定计划，跨场景评估被变更证据和词法根因匹配掩盖 |
| IC-P1-02 | P1 | `GraphNodes.collect_evidence()` 传入 `remaining_tool_calls=1`，使 `ToolRegistry.execute()` 的重试逻辑在真实 Graph 路径永不触发 |
| IC-P1-03 | P1 | API 要求调用方预先提供服务和时间窗；`parse_incident()` 没有完成 PRD 声明的自然语言解析 |
| IC-P1-04 | P1 | 默认假设只填 supporting evidence，`contradicting_evidence_ids` 与 `rejected_hypotheses` 没有形成可演示闭环 |
| IC-P1-05 | P1 | 模型上下文只使用首个服务，报告却把全部输入服务列为 affected，存在多服务结论过报风险 |

完整的问题影响、证据、复现、修复建议和验收标准见 [09-OPTIMIZATION-PLAN.md](09-OPTIMIZATION-PLAN.md)。

## 功能声明与实现对照

状态只使用约定枚举。

| 能力 | 状态 | 审查证据与限定 |
|---|---|---|
| FastAPI 创建/查询/恢复调查 | 完整实现 | `api/routes/investigations.py`、`application/investigation_service.py`；API Demo 实跑通过 |
| SSE 增量事件与断线续读 | 完整实现 | `astream(stream_mode="updates")` 事件投影；实跑收到 50 个事件，测试覆盖 `Last-Event-ID` |
| 七类调查工具 | 完整实现 | 日志、指标、Trace、变更、拓扑、Runbook、相似事件均经统一 Registry |
| 多源并行取证与 barrier 聚合 | 完整实现 | `graph/builder.py` 使用 `Send` 分发，`aggregate_evidence` 后统一进入假设生成 |
| 有界研究循环与预算停止 | 完整实现 | `judge_evidence` / `refine_investigation` 条件路由与轮次、工具、模型、deadline 限制 |
| HITL 暂停、接受、继续研究 | 完整实现 | `human_review()` 使用 `interrupt()` 和 `Command`；API 初次与 resume 的 run ID 不同 |
| checkpoint 同 thread 恢复 | 完整实现 | 内存 checkpointer 实跑/测试有效；Postgres 实际连接本次无法验证 |
| 自然语言事件解析 | 部分实现 | 只校验已结构化的 services/time window；不从 `raw_query` 推断这些字段 |
| 多服务联合调查 | 部分实现 | 输入模型允许多个服务，但模型上下文只取 `services[0]` |
| 支持证据驱动根因报告 | 完整实现 | 报告引用实际 `EvidenceRef`；支付默认链路可复现 |
| 反证与被淘汰假设 | 占位实现 | 类型和字段存在，默认 provider 与报告生成未产出实际 rejected hypothesis |
| 引用定位到来源 | 部分实现 | URI/locator/hash 字段齐全；没有 Evidence Store/resolver，哈希真实性未校验 |
| 引用内容完整性校验 | 未实现 | 只比较两个对象中的 hash 字符串；fixture 存在明显占位值 |
| BM25 + 向量 + RRF 混合检索 | 完整实现 | 本次检索实跑同时命中 BM25/vector，过滤与 citation 可见 |
| 查询改写 | 部分实现 | 确定性别名表可运行，但明显偏 payment/database-pool |
| rerank | 未实现 | 架构文档提到可选 rerank，源码无 reranker |
| 上下文压缩 | 未实现 | 仅有长度截断和有界 state，不是语义压缩 |
| 增量/幂等知识摄取 | 完整实现 | 同内容重复 ingest 数量稳定；按内容哈希替换 |
| pgvector 存储适配器 | 部分实现 | 有参数化 SQL 与 recording fake 合同测试；未接默认链路、无本次真实 DB 验证 |
| 真实 Prometheus 适配器 | 部分实现 | HTTP 适配器与 fake transport 测试存在；本次未启动 live OTLP/Prometheus 栈 |
| 真实 LLM Provider | 未实现 | 只有 `FakeModelProvider`；`model_api_key` 未接入 |
| 离线评估流水线 | 完整实现 | 3/3 样本完成且失败样本可保留；仅能视为回归评估 |
| 鉴权与 API 限流 | 未实现 | 默认 localhost 适合演示，不适合公网部署 |
| 持久化调查 Repository | 未实现 | 应用层固定使用内存 repository；checkpoint 与调查记录持久化是两件事 |

## 实际检查结果

| 命令/链路 | 真实结果 |
|---|---|
| `uv lock --check` | 通过，解析 74 个包 |
| `uv run ruff format --check .` | 通过，110 个文件已格式化 |
| `uv run ruff check .` | 通过 |
| `uv run mypy src tests scripts` | 通过，110 个源码文件无问题 |
| `uv run pytest` | **196 passed in 3.37s** |
| `uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md` | 通过 |
| `docker compose config` | 通过 |
| fixture 调查 CLI | probable，13 条 supporting evidence，7 次工具调用，1 轮研究 |
| FastAPI + `scripts/run_api_demo.py` | 通过；创建、SSE、HITL、resume、完成报告均成功 |
| RAG ingest/search | 6 文档、18 chunks；重复 ingest 稳定；混合检索成功 |
| 离线评估 | 3/3 completed，0 failed；但存在本报告揭示的指标口径问题 |
| fixture canonical SHA-256 复算 | payment 12/12 匹配；checkout 0/5、inventory 0/5、example 0/1 匹配 |
| 学习文档纯校验 | **失败**：缺少 `src/incident_copilot/core/clock.py` 的源码精读链接 |

第一次在受限 sandbox 内调用 `uv.exe` 遇到操作系统拒绝访问；在获准的非 sandbox 进程中原命令全部成功。这是审查环境限制，不是仓库失败。Docker 本次只验证 Compose 配置，没有声称 live Prometheus/Postgres 已启动成功。
