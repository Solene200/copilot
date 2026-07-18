# IncidentCopilot 实施进度

## 当前状态

| 项目 | 值 |
| --- | --- |
| 当前已完成阶段 | Phase 7 |
| 下一阶段 | 无；等待用户定义后续范围 |
| 最近更新 | 2026-07-18 |
| 仓库初始状态 | 空目录，无 `.git` 元数据 |
| 当前运行环境 | Windows / PowerShell；Python 3.13 可用 |

## Phase 5–7 独立严格审查（2026-07-18）

- P0：未发现。
- 已修复 P1：人工审核追加查询此前只写入 State、未进入规划上下文；现由 `ModelContext` 显式携带 judge 的下一步查询和人工反馈，离线 Fake Model 会生成受限真实步骤，集成测试断言实际参数。
- 已修复 P1：后台调查任务此前没有应用关闭回收，初始化异常可能悬挂，Graph 无 `final_report` 也会被标记完成；现统一取消/await 任务、观察后台异常，并把缺报告或初始化失败转换为明确 `failed` 终态。
- 已修复 P1：Evaluation 检索过滤此前读取 ground truth 服务标签，多轮同名工具参数又被最后一次调用覆盖，且早期轮次无法从最新 plan 反查；现过滤只来自 `IncidentContext`，`StepResult` 保留有界实际参数，参数 evaluator 在所有真实同名调用中选择最佳字段匹配，并有标签污染与多轮 Runner 回归测试。
- 已修复 P1：Prometheus 返回序列此前未校验响应中的服务标签、请求时间窗和时间顺序；现把不匹配响应作为 malformed response 拒绝。Demo emitter 关闭路径确保即使 flush 失败也调用 shutdown。
- 已修复 P1：严格审查第一次真实 Compose 冷启动暴露 Prometheus 健康与首批稳定 scrape 之间的竞态，demo 虽探测到一次序列但 Graph 随后降级为无 metrics。readiness 现要求间隔两个 scrape 周期的连续成功，并有单测；从空容器/空卷重新构建复跑后退出码 0，Graph 返回真实 `ev_prom_*`、`evidence_sufficient`、7 次工具调用和 13 条 citation。
- 已修复文档状态：路线图总览与本页顶部统一为 Phase 7 已完成；未创建或实现后续 Phase。
- 定向回归：45 passed；后台关闭补充测试单独为 8 passed。
- 当前全量门禁：`uv lock --check` 解析 74 个包；Ruff format/check 通过；`mypy src tests` 通过 98 个 source files；demo scripts mypy 通过 9 个 source files；`pytest` 为 194 passed、0 failed、0 warning；当前 Graph Mermaid 一致性检查通过。
- 当前离线 Evaluation 审查运行：3/3 completed、0 failed；结果写入系统临时目录，未覆盖历史 Phase 6 基线。Compose 配置解析退出码为 0；真实冷启动修复后退出码也为 0。最终执行 `down -v --remove-orphans`，只删除本次审查创建的容器、网络和 Prometheus 演示卷，确认项目无容器或卷残留。

## Phase 0 — 需求、架构和项目规范

### 状态

`completed`

### 完成内容

- 将原始设想整理为产品目标、角色、场景、功能/非功能需求和明确范围边界。
- 定义离线 fixture、Docker 集成、外部数据源三种运行模式。
- 定义 API、Investigation Service、LangGraph、Model、Tool/Provider、RAG、Repository 和 Checkpointer 的职责边界。
- 设计动态并行取证、证据聚合、假设验证、研究循环、预算路由、报告与 HITL 主图。
- 明确 State 的覆盖/reducer 字段、原始证据外置、有界上下文和父图/子图策略。
- 定义 Incident、Evidence、Hypothesis、Report 及计划、引用、错误、知识 Chunk 的字段与不变量。
- 核验 2026-07-18 的主要依赖发布线，确定“兼容范围 + uv 锁文件 + Phase 1 实测”的版本策略。
- 为 Phase 1–7 定义输入条件、产出、关键测试和完成定义。

### 新增文件

- `AGENTS.md`
- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/GRAPH_DESIGN.md`
- `docs/DATA_MODEL.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`

### 实际检查结果

| 检查 | 结果 |
| --- | --- |
| 必需文件与非空检查 | PASS：7/7 文件存在且非空 |
| Markdown 标题/尾随空白/Tab 检查 | PASS：7 个 Markdown 文件，0 项失败 |
| 相对 Markdown 链接目标检查 | PASS：0 个断链 |
| Mermaid fence 配对与必需图检查 | PASS：架构图 1、Graph 图 1，0 个未闭合 fence |
| 必需主题与 Phase 状态一致性检查 | PASS：0 项失败 |
| 敏感信息模式扫描 | PASS：未发现疑似硬编码密钥 |
| Phase 1 文件越界检查 | PASS：未发现业务代码、依赖或容器文件 |
| Ruff / mypy / pytest | 不适用：Phase 0 没有 Python 项目或业务代码 |

检查由 PowerShell 只读脚本完成，脚本遍历全部 Markdown，检查 H1、空白、相对链接、Mermaid fence、必需主题、路线图状态、敏感模式和越界路径。两次检查的终端摘要分别为 `PHASE_0_DOC_CHECKS=PASS` 与 `PHASE_0_CONSISTENCY=PASS`。

### 环境事实

- 2026-07-18 已通过 winget 安装并验证 Git 2.55.0、uv 0.11.29、Docker Desktop 4.82.0 和 Microsoft WSL 2.7.10。
- 当前 Python 为 3.13.0；uv 能定位到 `D:\mysoftwares\python\python3.13\python.exe`。
- Docker CLI 29.6.1 与 Docker Compose 5.3.0 可执行，当前用户已加入 `docker-users`。
- Docker Desktop 服务尚未首次启动；首次运行可能要求完成界面初始化或重启 Windows，不能据此声称 Docker daemon 已就绪。
- 当前 Codex 进程在安装前启动，因此可能需要新开终端后才会通过短命令解析新 PATH。
- 环境完整性复检结果为 `ENVIRONMENT_SETUP_CHECK=PASS`；同时确认没有创建任何 Phase 1+ 文件。
- Mermaid 源码已做 fence 和人工结构检查，但未用 CLI 实际渲染；该环境没有渲染器。这是文档工具限制，不影响 Phase 0 其它门禁。

### 已知问题与后续决策

- 版本表是已核验的解析基线，不代表依赖组合已安装；Phase 1 必须用 uv 生成锁文件并跑导入/测试。
- Redis 是否为必需组件留到 Phase 5 以跨进程 SSE/任务需求实测决定。
- Checkpointer 的默认本地/集成后端留到 Phase 5 决定，但目标持久化后端为 PostgreSQL。
- 真实 Provider 类型留到 Phase 7 根据 OpenTelemetry Demo 的可复现性选择。
- 性能和准确率尚无数据；当前文档中的数字只标记为目标或建议默认值。

### 下一阶段输入条件

开始 Phase 1 前必须具备：

1. 用户明确确认进入 Phase 1。
2. 新开终端验证 `git --version` 与 `uv --version` 可通过 PATH 直接运行。
3. 重新核验核心依赖范围，用真实解析生成 `uv.lock`。
4. 只实施 Phase 1 的工程骨架、领域模型、`/health` 与基础测试，不提前实现 Provider/RAG/Graph。

## 变更日志

| 日期 | Phase | 摘要 |
| --- | --- | --- |
| 2026-07-18 | 0 | 从空目录建立需求、架构、Graph、数据模型、路线和仓库规范基线 |
| 2026-07-18 | 环境准备 | 安装并验证 Git、uv、WSL 2、Docker Desktop/CLI/Compose；未开始 Phase 1 |
| 2026-07-18 | 2 | 完成离线 Fixture Provider、七工具、Registry、基准 payment-service 数据与失败测试 |
| 2026-07-18 | 3 | 完成离线知识 ingest、Fake Embedding、BM25/向量/RRF、pgvector Adapter 与检索脚本 |
| 2026-07-18 | 2/3 审查 | 独立审查并修复工具输出边界、CJK 切分、异步阻塞、向量版本隔离、ingest 原子性及运行时 DDL；99 项离线测试通过 |
| 2026-07-18 | 4 | 完成 LangGraph Send 并行调查、有界研究循环、结构化 Fake Model、降级报告和源码 Mermaid；121 项离线测试通过 |
| 2026-07-18 | 4 审查 | 修复 deadline/模型故障边界、Send 批次丢失、冲突 reducer、计划身份信任、调用前 Token 预算和硬编码答案；130 项离线测试通过 |

## Phase 1 — 工程骨架和领域模型

### 状态

`completed`

### 完成内容

- 建立 Python `src` layout、Hatchling 构建配置、uv 依赖组和精确锁文件。
- 建立 FastAPI app factory 与默认 ASGI app；`/health` 是不依赖外部服务的进程存活检查。
- 使用 Pydantic Settings 管理 `INCIDENT_COPILOT_` 前缀配置，模型密钥保持可选且不出现在 repr。
- 使用标准库 logging 输出 JSON，对消息及嵌套 extra 中的凭据做脱敏，同时保留 `input_tokens`、`token_usage` 等非秘密 AI 可观测字段。
- 定义框架无关的应用异常层次，以及 FastAPI 的应用异常、请求校验异常和未知异常安全映射；已知异常在 API 边界再次执行秘密脱敏。
- 实现 Incident、Evidence/Citation/EvidenceRef、Hypothesis/VerificationQuery、IncidentReport 及其子模型。
- 所有领域时间拒绝 naive datetime；ID、时间窗口、分数、证据关系、时间线和调查统计均有 Schema 不变量。领域值对象冻结，需要维持集合不变量的序列和统计映射不可原地修改。
- 定义版本化 `IncidentFixture` 与 evaluation-only ground truth，并提供一个脱敏 JSON 结构样例。
- 配置 Ruff、mypy strict、pytest 与 pytest-asyncio；测试全程不访问付费 API 或外部服务。

### 新增或修改文件

- 工程：`pyproject.toml`、`uv.lock`、`.gitignore`、`.env.example`、`Makefile`、`README.md`、`LICENSE`。
- 应用：`src/incident_copilot/main.py`、`api/`、`core/`。
- 领域：`src/incident_copilot/domain/`。
- Fixture：`src/incident_copilot/fixtures/`、`data/README.md`、`data/incidents/example.json`。
- 测试：`tests/unit/`、`tests/integration/test_api.py`。
- 文档：`AGENTS.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。

### 依赖解析结果

2026-07-18 使用 Python 3.13.0 与 uv 0.11.29 实际解析 37 个包。直接依赖的最终锁定版本包括：FastAPI 0.139.2、Pydantic 2.13.4、pydantic-settings 2.14.2、Uvicorn 0.51.0、Ruff 0.15.22、mypy 2.3.0、pytest 9.1.1、pytest-asyncio 1.4.0 和 httpx2 2.7.0。

初次测试使用旧 `httpx` 时出现 Starlette 弃用警告；已切换到当前 `httpx2` 并重新锁定，最终测试为 0 warning。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv sync` | PASS：创建 `.venv`，解析 37 个包并安装项目 |
| `uv lock --check` | PASS：锁文件与项目元数据一致 |
| `uv run ruff format --check .` | PASS：27 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS：All checks passed |
| `uv run mypy src` | PASS：19 个 source files，0 issues |
| `uv run mypy src tests` | PASS：27 个 source files，0 issues |
| `uv run pytest` | PASS：43 passed，0 warning，最终复检 0.46s |
| 独立 Uvicorn + HTTP `/health` | PASS：真实 TCP 请求返回 `status=ok`、版本 0.1.0、test 环境 |

上述耗时只是本机测试运行记录，不是性能基准或项目性能声明。

### 已知问题

- 当前仓库是有效 Git worktree；本次审查开始时 `git status --short` 为空，基线提交为 `f3d4055 feat: initialize IncidentCopilot phase 1`。
- Docker Desktop 因固件/Windows 虚拟化支持尚未启用而不能启动；Phase 1 不依赖 Docker，Phase 7 前需按系统说明修复。
- 当前 Codex 沙箱不能直接执行用户 winget 目录中的 uv，自动化检查使用了已验证的 uv 绝对路径；普通新终端应通过用户 PATH 运行 `uv`。
- `Makefile` 是跨平台命令速记；Windows 若没有 `make`，README 中的原始 uv 命令仍是受支持入口。
- P2：`X-Request-ID` 尚未限制长度/字符；`api_prefix` 在 Phase 1 尚无消费者；报告 timeline/evidence/citations 的跨集合引用完整性待 Phase 4 报告生成策略定稿。
- P2：Pydantic 冻结是浅层冻结，`Evidence.content` 与 `metadata` 的嵌套 JSON 仍可变；深层不可变或副本策略待 Evidence Store 实现时确定。

### 手动验证

```text
uv sync
uv run uvicorn incident_copilot.main:app --reload
```

浏览 `http://127.0.0.1:8000/health`，预期 HTTP 200；浏览 `/docs` 可查看 OpenAPI 页面。停止服务使用 `Ctrl+C`。

### 下一阶段输入条件

开始 Phase 2 前必须具备：

1. 用户明确确认进入 Phase 2。
2. 保持 Phase 1 的全部质量门禁通过。
3. 为 payment-service 场景设计脱敏且含噪声的日志、指标、Trace、变更和拓扑原始 fixture。
4. 只实现 Provider Protocol、Fixture Provider、七类工具、Registry、超时/错误与单元测试，不提前实现 RAG 或 LangGraph。

## Phase 1 变更日志

| 日期 | 摘要 |
| --- | --- |
| 2026-07-18 | 完成工程骨架、领域模型、Fixture Schema、health API 与 29 项离线测试 |
| 2026-07-18 | 独立审查 Phase 1，修复领域可变性、EvidenceRef/Citation/统计不变量及日志/API 脱敏边界；43 项离线测试通过 |

## Phase 2 — Fixture Provider 和工具层

### 状态

`completed`

### 开始前基线

- 完整重读 `AGENTS.md`、PRD、架构、Graph、数据模型、路线图和进度文档。
- 当前 `main` 跟踪 `origin/main`；工作区已有 17 个未提交的 Phase 1 加固文件（309 行新增、73 行删除），无未跟踪文件。本阶段保留并兼容这些既有修改，没有回退或覆盖。
- 基线真实门禁：`uv lock --check`、Ruff format/check、`mypy src tests` 全部通过，Phase 1 全量测试 `43 passed`。

### 完成内容

- 定义 Log、Metrics、Trace、Change、Topology、Knowledge 六个异步 Provider Protocol；知识端口分别支持 Runbook 与历史事故。
- 定义七个严格 Pydantic 输入 Schema：服务名、timezone-aware 时间、24 小时窗口、limit、拓扑深度、指标/Trace/变更过滤和历史回看均有边界。
- 实现 allow-list Tool Registry，提供重名/未知工具保护、参数校验、per-call deadline、单次 timeout、最多有限重试、指数退避、调用预算、输出来源及请求 service/time/limit 范围校验、错误归一化和结构化遥测。
- 实现单一版本化 IncidentFixture 驱动的 FixtureProvider；所有过滤、排序和 limit 都是确定性的，空结果不会伪造证据。
- 注册 `search_logs`、`query_metrics`、`query_traces`、`get_service_topology`、`get_recent_changes`、`search_runbooks`、`search_similar_incidents` 七个只读工具。
- 建立 payment-service 连接池耗尽场景：12 条证据覆盖日志、指标、Trace、变更、拓扑和知识，并包含网关健康反证、健康检查/业务拒绝噪声。
- 每条 fixture Evidence 包含来源名称/类型、时间点或窗口、服务、可解析 Citation，以及由规范化 content 实际计算的 SHA-256；ground truth 不经 Provider 工具返回。
- 测试全程使用 fixture 或受控内存 handler，不调用网络、数据库、真实可观测平台或付费 API。

### 新增或修改文件

- 工具契约与实现：`src/incident_copilot/tools/`。
- Fixture 数据：`data/incidents/payment-service-pool-exhaustion.json`、`data/README.md`。
- 测试：`tests/unit/tools/`、`tests/integration/test_fixture_tools.py`。
- 文档：`AGENTS.md`、`README.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv lock --check` | PASS：锁文件与项目元数据一致；Phase 2 未新增依赖 |
| `uv run ruff format --check .` | PASS |
| `uv run ruff check .` | PASS |
| `uv run mypy src tests` | PASS：54 个 source files，0 issues |
| `uv run pytest tests/unit/tools tests/integration/test_fixture_tools.py` | PASS：27 passed，严格审查复检 0.23s |
| `uv run pytest` | PASS：99 passed，严格审查复检 0.86s，0 warning |

测试耗时只作为本机命令记录，不作为性能基准或 P95 声明。

### 已知问题

- `search_runbooks` 和 `search_similar_incidents` 当前只是确定性 fixture 文本/metadata 过滤；BM25、向量检索、RRF、Chunk 和检索评估属于 Phase 3。
- Registry 当前校验单次调用的 `remaining_tool_calls` 和 deadline，不维护调查级共享计数；Phase 4 的 Graph State/reducer 才负责全局预算原子更新。
- FixtureProvider 适合小型离线演示，启动时整体加载 JSON；未实现 Evidence Store、持久化或真实 Provider。
- Docker Desktop 的虚拟化问题仍未解决，但 Phase 2 不依赖 Docker。
- 本阶段没有测量或声明调查性能、诊断准确率、Recall 或 MRR。

### 手动验证

```text
uv sync
uv run pytest tests/unit/tools tests/integration/test_fixture_tools.py
uv run pytest
```

检查 `data/incidents/payment-service-pool-exhaustion.json` 可审阅固定时间线、反证/噪声和 evaluation-only ground truth。工具还没有 HTTP API；Phase 2 的受支持调用入口是 `ToolRegistry.execute(...)`，其端到端用法由 `tests/integration/test_fixture_tools.py` 覆盖。

### 下一阶段输入条件

开始 Phase 3 前必须具备：

1. 用户明确确认进入 Phase 3。
2. 保持 Phase 2 的 Provider/Registry 契约与全部质量门禁通过。
3. 只实现知识文档加载、切分、确定性 Fake Embedding、BM25/向量/RRF、过滤去重和 citation 保留。
4. 不提前实现 LangGraph 调查流程、API 生命周期或真实模型调用。

## Phase 3 — RAG Indexing 和 Retrieval

### 状态

`completed`

### 开始前基线

- 完整重读 `AGENTS.md`、PRD、架构、Graph、数据模型、路线图和进度文档。
- `main` 与 `origin/main` 一致，工作区无 diff；基线提交为 `121148c feat: complete phase 2 provider tool layer`。
- 基线 `uv lock --check`、Ruff format/check、`mypy src tests` 全部通过，全量测试 `66 passed`。

### 完成内容

- 定义 `KnowledgeDocument`、`KnowledgeChunk`、`EmbeddedChunk`、metadata filter、查询、候选、命中和 ingest/result Schema；时间、URI、服务、环境、hash 和引用均严格校验。
- 使用标准库 `tomllib` 加载 UTF-8 Markdown frontmatter，限制文件位于配置根目录内，并在完整读取前执行文件大小上限检查；拒绝坏 TOML、缺失 metadata 和重复 document ID。
- 实现按 Markdown 标题边界切分的 Splitter；只在超长小节内按同一 token 规则执行有界 overlap，包含无空格 CJK 文本时仍保证 Chunk 上限；每个 Chunk 继承文档 metadata 并生成可解析 citation。
- 实现固定 64 维 signed-hash Fake Embedding，明确只用于确定性数据链路，不声明真实语义质量。
- 实现 BM25、内存 cosine VectorStore、统一 metadata filter、稳定 tie-break、RRF 融合、content-hash 去重、top_k 和 citation 保留；向量按 embedding model/version 隔离并拒绝非有限值、零向量及非正相似度误命中。
- 实现透明规则 Query Rewrite，覆盖 `db/postgresql/timeout/pool/checkout` 和 payment-service 场景中的中文别名，不调用 LLM。
- 实现 `RagKnowledgeProvider`，保持 Phase 2 `KnowledgeProvider` 与两个工具的调用契约；同步 BM25/向量/RRF 工作通过 worker thread 隔离，避免直接阻塞事件循环。
- 实现 `PgVectorStore`：安全表名、维度/embedding 版本校验、事务式文档替换、参数化 SQL、JSONB payload 和 pgvector cosine 查询；Adapter 不执行运行时 DDL，schema 必须由 Alembic migration 预置，默认无驱动/数据库依赖。
- 准备 2 个 Runbook、1 个服务说明和 1 个历史事故，共加载为 12 个 Chunk。
- 提供 `scripts/ingest_knowledge.py` 和 `scripts/search_knowledge.py`；实际运行输出可审计 JSON，不写入伪造索引/评估文件。
- 将 pytest `basetemp` 固定到仓库内已忽略的 `.pytest-tmp/`，避免 Windows 用户临时目录 ACL 导致 Loader 的真实临时文件测试无法运行；未跳过测试。
- 测试默认仅使用本地 Markdown、Fake Embedding、内存索引或 recording SQL session，不调用网络、在线模型、在线 embedding 或付费 API。

### 分步 Git 记录

- `048e5e3`：知识 Schema、Loader、Splitter、metadata/hash/citation 和 4 个样例文档；相关 8 tests、全量 74 tests 通过后推送。
- `bc4bb9d`：Fake Embedding、BM25、内存/pgvector、Query Rewrite、RRF Hybrid Retrieval、RAG Provider；相关 21 tests、全量 87 tests 通过后推送。
- 最终脚本与阶段文档在完整门禁通过后单独提交推送。

### 新增或修改文件

- RAG：`src/incident_copilot/rag/`。
- 知识数据：`data/knowledge/runbooks/`、`data/knowledge/services/`、`data/knowledge/incidents/`。
- 脚本：`scripts/ingest_knowledge.py`、`scripts/search_knowledge.py`。
- 测试：`tests/unit/rag/`、`tests/integration/test_rag_pipeline.py`。
- 文档/入口：`README.md`、`data/README.md`、`Makefile`、`.gitignore`、`pyproject.toml`、`AGENTS.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。

### 依赖与运行影响

- Phase 3 未新增第三方依赖，`uv.lock` 仍解析 37 个包。
- Fake Embedding、BM25 与内存向量索引使用 Python 标准库，默认启动/测试不需要 API Key、数据库或网络。
- pgvector Adapter 面向窄 `PgVectorSession` Protocol；真实部署可由 psycopg/SQLAlchemy 包装器注入，避免把数据库驱动变成离线必需依赖。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv lock --check` | PASS：锁文件一致，37 packages |
| `uv run ruff format --check .` | PASS：56 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS |
| `uv run mypy src tests` | PASS：54 个 source files，0 issues |
| `uv run pytest tests/unit/rag tests/integration/test_rag_pipeline.py` | PASS：29 passed，严格审查复检 0.39s |
| `uv run pytest` | PASS：99 passed，严格审查复检 0.86s，0 warning |
| `uv run python scripts/ingest_knowledge.py` | PASS：4 documents、12 chunks、重复 ingest 计数一致 |
| `uv run python scripts/search_knowledge.py --query "database connection pool timeout" --service payment-service --document-type runbook --top-k 2` | PASS：返回 2 条 Runbook Chunk，citation 可解析到源 Markdown |

测试耗时只作为本机运行记录，不作为性能/P95 声明。

### 固定检索回归结果

3 条手写查询分别期望数据库连接池 Runbook、payment-service 服务文档和历史连接池事故。最终离线结果：

- Recall@3：`1.0`（3/3 目标文档进入前三）；
- MRR：`7/9 ≈ 0.7778`（目标文档排名分别为 3、1、1）。

该结果只描述当前 4 文档、3 查询的确定性回归 fixture，不是模型准确率、生产检索质量或统计显著评估。完整 Evaluation 仍属于 Phase 6。

### 已知问题

- Fake Embedding 是 signed-hash 词袋，不能代表真实语义 embedding；中文能力来自有限规则 rewrite，哈希碰撞仍可能让无关文本产生正相似度。
- 默认索引在内存中，每个进程重新 ingest；未实现持久化快照、增量文件监控或并发 ingest/search 协调。
- `PgVectorStore` 的参数化 SQL 和 transaction contract 已用 recording session 验证，但当前机器没有可用 PostgreSQL/pgvector，因此未运行真实数据库集成测试；真实部署还需要在持久化阶段提供 Alembic migration。
- `RagKnowledgeProvider` 使用 `asyncio.to_thread` 避免事件循环被同步检索阻塞；调用方超时可以及时返回，但 Python worker thread 不能被强制取消，生产适配器仍需自身超时与资源边界。
- Splitter 使用确定性近似 token 计数，不等同于未来模型 tokenizer；reranker 尚未实现且在 Phase 3 为可选项。
- Hybrid `score` 是归一化 RRF 排序分数，不是概率或诊断置信度。
- 未实现 LangGraph、模型推理、调查预算/循环或最终报告；这些属于 Phase 4。

### 手动验证

```text
uv sync
uv run python scripts/ingest_knowledge.py
uv run python scripts/search_knowledge.py --query "database connection pool timeout" --service payment-service --document-type runbook --top-k 2
uv run pytest tests/unit/rag tests/integration/test_rag_pipeline.py
```

预期初始化脚本报告 4 documents、12 chunks 和 `repeated_ingest_same_counts=true`；检索脚本输出 rewritten query、RRF 匹配来源、section、原文及 `internal://knowledge/...` citation。

### 下一阶段输入条件

开始 Phase 4 前必须具备：

1. 用户明确确认进入 Phase 4。
2. 保持 Phase 2 工具和 Phase 3 RAG 契约及全部质量门禁通过。
3. 先使用 Fixture Provider、RagKnowledgeProvider 和可复现 Fake Model 实现有界 LangGraph 调查循环。
4. 不提前实现 Phase 5 的 HTTP 调查生命周期、SSE、checkpoint 后端或 HITL API。

## Phase 4 — LangGraph 调查工作流

### 状态

`completed`

### 开始前基线

- 完整重读 `AGENTS.md`、PRD、架构、Graph、数据模型、路线图和进度文档。
- 审计发现 `HEAD 9597b83` 之上有 14 个未提交的 Phase 2/3 严格审查加固文件；先核对 diff 并跑完整门禁，确认 `99 passed` 后以 `a62f932` 独立提交推送，没有混入 Phase 4。
- 干净基线为 `a62f932`，`main` 跟踪 `origin/main`；Ruff、mypy 和 99 项全量测试通过。

### 完成内容

- 新增并由 uv 锁定 LangGraph 1.2.9；当前锁文件解析 61 个包。LangGraph 许可证为 MIT，默认运行不启用 LangSmith 网络追踪，也不依赖在线模型 SDK。
- 建立 `ModelProvider` Protocol、四类 Pydantic 结构化输出和确定性 Fake Model。模型响应一律视为不可信 JSON；模型超时、运行异常或校验失败时每任务最多尝试 2 次，之后使用显式规则降级并写入结构化 Error。
- 建立 `InvestigationState`、稳定 ID 去重/有界 reducer、并行增量计数和估算 Token usage reducer。同 ID 冲突载荷使用规范化内容确定性裁决，measured usage 的加法单位元保持 `estimated=false`。Graph State 只保存 `EvidenceRef`，不保存原始日志、span、指标序列或文档正文对象。
- 实现 `parse_incident → build_investigation_plan → Send collect_evidence → aggregate_evidence → generate_hypotheses → verify_hypotheses → judge_evidence → refine/generate_report` 实际图。
- `dispatch_evidence_collection` 在发送前按剩余工具预算和并发上限选择批次；同一轮返回多个最小作用域 `Send`，聚合后继续发送下一批，计划步骤不会因低并发配置静默丢失。异步栅栏测试只有在 7 个 Provider 调用同时开始后才放行，因此不是基于耗时猜测并行。
- 研究路由是纯函数，优先处理 deadline、工具、模型调用、估算 Token、充分性和最大轮数；已过期 invocation 不执行工具或外部模型。模型不能返回节点名、修改预算或控制 step/query identity 与 round。
- 模型调用由 Graph 使用剩余总 deadline 包装；调用前估算输入 Token，校验重试前再次检查累计 usage。`QueryContext.remaining_tool_calls` 同时限制 Registry 的真实 Provider 尝试次数。
- 假设验证会过滤不存在的 Evidence 外键并按独立来源降置信度；最终报告只附加 State 中存在的 Evidence ID/Citation，并在错误或预算停止时写明 limitation。受限报告清除未证实 root cause 且置信度不超过 0.55，report 节点本次错误也计入 limitation。
- 离线装配同时使用 Fixture Provider、Phase 3 `RagKnowledgeProvider` 和 Fake Model；单 Provider 失败不会取消同轮其它分支。
- 提供完整调查脚本和当前源码 Mermaid 脚本；`GRAPH_CURRENT.md` 由 `draw_mermaid()` 输出生成并由测试逐字符防漂移，没有绘制 Phase 5 的 HITL、checkpoint 或 API。
- 未实现调查 HTTP API、SSE、后台生命周期、checkpoint、interrupt/Command 恢复或人工审核；这些仍属于 Phase 5。

### 分步 Git 记录

- `495a16e`：新增并锁定 LangGraph 1.2.9 运行时。
- `93ee1f3`：结构化模型契约、State reducer、预算路由和 11 项单测。
- `372f69d`：动态 Send 调查节点、二次研究、失败降级和端到端测试。
- `d60a29d`：离线演示、源码 Mermaid 及文档漂移测试。
- `e6f0876`：补齐估算 Token 预算停止边界。

### 新增或修改文件

- Graph：`src/incident_copilot/graph/`。
- 测试：`tests/unit/graph/`、`tests/integration/test_investigation_graph.py`、`tests/integration/test_graph_mermaid.py`。
- 脚本：`scripts/run_investigation.py`、`scripts/render_graph.py`。
- 文档/入口：`docs/GRAPH_CURRENT.md`、`README.md`、`Makefile`、`AGENTS.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。
- 依赖：`pyproject.toml`、uv 生成的 `uv.lock`。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv sync` | PASS：解析 61 个包，检查 60 个已安装包 |
| `uv lock --check` | PASS：锁文件与项目元数据一致，61 packages |
| `uv run ruff format --check .` | PASS：73 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS：All checks passed |
| `uv run mypy src tests scripts` | PASS：73 个 source files，0 issues |
| `uv run pytest tests/unit/graph tests/unit/tools/test_registry.py tests/integration/test_investigation_graph.py tests/integration/test_graph_mermaid.py` | PASS：47 passed，0 warning，最终复检 1.00s |
| `uv run pytest` | PASS：130 passed，0 warning，严格审查复检 1.49s |
| `uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md` | PASS：文档图与当前编译 Graph 一致 |
| `uv run python scripts/run_investigation.py` | PASS：输出合法 `IncidentReport`；`probable`、1 轮、7 工具、4 Fake Model、13 条六类 Evidence、停止原因为 `evidence_sufficient` |

上述耗时仅是本机测试运行记录，不是性能基准。Fake Model usage 明确标记 `estimated=true`；本阶段没有测量或声明诊断准确率、P95、真实模型成本或泛化质量。

### 已知问题

- Fake Planner 是 payment-service 演示用确定性配方，不代表真实 LLM 的规划或诊断能力；Fake Hypothesis 从高相关 Evidence summary 派生且不读取 evaluation ground truth。当前只有 Provider-neutral Protocol，没有真实模型 Adapter。
- Token usage 由字符数近似并明确标记 estimated；Graph 会在首次调用及结构化重试前检查可估算输入，但不能预知单次真实模型的输出 Token。异步模型任务可在 deadline 取消；若未来 Adapter 在 async 方法内执行不可取消的同步阻塞，仍必须由 Adapter 自身隔离。
- Evidence reducer 是确定性全局 top-100，不保证每类来源配额；极端高扇出时 `StepResult.evidence_ids` 可能引用已从 State 裁剪的 EvidenceRef。
- 结构化重试使用相同 ModelContext，没有把上次校验错误作为 repair feedback；这是可审计重试，不是完整的自动修复提示链。
- Graph 当前单进程、无 checkpoint；进程恢复、稳定 thread/run ID、interrupt、HITL 和 SSE 属于 Phase 5。
- 并行测试证明分支并发启动，但没有进行吞吐量、时延或扩展性基准测试。
- 报告保留 EvidenceRef/Citation，但原始 Evidence Store 和持久化 Repository 尚未实现。
- Docker Desktop 的虚拟化问题仍不影响 Phase 4 离线路径；真实 PostgreSQL/checkpointer 集成前仍需修复宿主机虚拟化。

### 手动验证

```text
uv sync
uv run python scripts/run_investigation.py
uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md
uv run pytest tests/unit/graph tests/unit/tools/test_registry.py tests/integration/test_investigation_graph.py tests/integration/test_graph_mermaid.py
```

调查脚本预期输出 JSON `IncidentReport`，其中 `supporting_evidence[*].evidence_id`、timeline Evidence ID 和 citations 均可回指本次 State。当前没有调查 API；`uvicorn` 仍只提供 Phase 1 的 `/health` 和 OpenAPI。

### 下一阶段输入条件

开始 Phase 5 前必须具备：

1. 用户明确确认进入 Phase 5。
2. 保持 Phase 4 的 130 项离线测试与 Mermaid 一致性门禁通过。
3. 设计稳定的 investigation/thread/run ID、后台生命周期、SSE 事件和幂等 API 契约。
4. 只实现 API、Streaming、Checkpoint 和 HITL，不提前实现 Phase 6 Evaluation 或 Phase 7 真实数据源。

## Phase 5 — API、Streaming、Checkpoint 和 HITL

### 状态

`completed`

### 开始前基线

- 完整重读 `AGENTS.md`、PRD、架构、Graph、数据模型、路线图和进度文档，并检查 Git diff。
- 开始时发现 15 个尚未提交的 Phase 4 严格预算加固文件；先运行锁文件、Ruff、mypy 和全量测试，确认 `130 passed` 后以 `812235b` 独立提交推送，没有混入 Phase 5。
- Phase 5 的干净基线为 `812235b`，本阶段没有调用真实付费 API，也没有提前实现 Phase 6 Evaluation 或 Phase 7 真实 Provider。

### 完成内容

- 新增严格 `HumanFeedback` / `HumanReviewRequest`；高风险 remediation 经源码条件路由进入 `human_review`，节点调用 LangGraph `interrupt()`，接受后结束、追加调查后回到 refine 并再次暂停。旧 Phase 4 离线脚本仍显式使用无审核图，不破坏原演示入口。
- `build_investigation_graph` 可注入 `BaseCheckpointSaver` 和审核开关；默认 FastAPI 使用 `InMemorySaver`。可选 `postgres` extra 锁定官方 `langgraph-checkpoint-postgres` 3.1.0 / psycopg 3，并在 lifespan 内打开 `AsyncPostgresSaver`、执行 `setup()`、保持 saver 生命周期。
- 新增任务仓储与生命周期服务：pending/running/waiting_review/completed/failed 状态、乐观 version、幂等创建、逐调查恢复锁、后台执行、预算校验和失败降级。`investigation_id` / `thread_id` 共享 UUID，每次初始或恢复生成新 `run_id`。
- 应用可从稳定 thread checkpoint 重建丢失的暂停/完成任务元数据；测试使用同一 saver、新 Graph 和全新任务仓储完成恢复，不依赖原服务实例。
- 新增版本化安全事件：queued/started/node/tool/evidence/hypothesis/budget/review/report/failure；sequence 和 event ID 单调，Evidence 事件保留 source/time/service/citation。事件与状态响应递归脱敏，不发送原始 checkpoint State。
- 实现四个 `/api/v1/investigations` 端点。SSE 支持 `Last-Event-ID`、heartbeat、客户端断连停止，并在 waiting_review/completed/failed 静默点关闭当前连接；恢复后客户端可用最后 ID 续传。
- HTTP 契约覆盖幂等重放、载荷冲突、404、409、422、已有 500 统一异常、无效 cursor、非法反馈、追加调查无预算和重复恢复。
- 更新当前源码 Mermaid；图只展示真实 Graph 节点/边，checkpoint、后台任务和 SSE 保持为图外应用层。新增 `scripts/run_api_demo.py`，使用标准库 HTTP 客户端对本地 Uvicorn 执行创建→SSE→审核→报告。

### 分步 Git 记录

- `21c0311`：checkpoint-enabled HITL Graph、结构化审核 Schema 及暂停/二次调查测试。
- `15a95e5`：任务状态、仓储、有序安全事件和后台生命周期服务。
- `de17847`：创建、状态、SSE、恢复 API 与 HTTP 集成测试。
- `8e4d6fc`：内存/PostgreSQL checkpointer 装配、可选依赖和跨 Graph 实例恢复。
- `4c066ad`：稳定 investigation/thread 映射及从 checkpoint 重建任务元数据。
- `38c95a0`：SSE heartbeat/断连及状态响应敏感输入过滤加固。

### 新增或修改文件

- 领域/Graph：`src/incident_copilot/domain/review.py`、`src/incident_copilot/graph/`。
- 应用层：`src/incident_copilot/investigations/`、`src/incident_copilot/core/config.py`、`src/incident_copilot/core/exceptions.py`。
- API：`src/incident_copilot/api/investigation_schemas.py`、`src/incident_copilot/api/routes/investigations.py`、`src/incident_copilot/main.py`。
- 测试：`tests/unit/api/`、`tests/unit/investigations/`、`tests/unit/domain/test_review.py`、`tests/integration/test_human_review_graph.py`、`test_investigation_service.py`、`test_investigation_api_phase5.py`。
- 演示/文档：`scripts/run_api_demo.py`、`scripts/render_graph.py`、`docs/GRAPH_CURRENT.md`、`README.md`、`.env.example`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。
- 依赖：`pyproject.toml`、uv 生成的 `uv.lock`。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv sync` | PASS：解析 65 个包，默认环境检查 60 个已安装包；PostgreSQL extra 未作为默认依赖安装 |
| `uv lock --check` | PASS：锁文件与项目元数据一致，65 packages |
| `uv run ruff format --check .` | PASS：88 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS：All checks passed |
| `uv run mypy src tests scripts` | PASS：88 个 source files，0 issues |
| `uv run pytest tests/unit/api tests/unit/investigations tests/unit/domain/test_review.py tests/integration/test_human_review_graph.py tests/integration/test_investigation_service.py tests/integration/test_investigation_api_phase5.py` | PASS：18 passed |
| `uv run pytest` | PASS：148 passed，0 warning |
| `uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md` | PASS：文档 Mermaid 与 Phase 5 实际编译 Graph 一致 |
| 独立 Uvicorn `127.0.0.1:18765` + `scripts/run_api_demo.py` | PASS：50 个 SSE 事件；waiting_review→accept→completed；13 条 supporting evidence；初始/恢复 run ID 不同 |
| PostgreSQL 跨进程集成 | PASS：Docker PostgreSQL 18.4 / pgvector 0.8.5；应用进程重建后同一 thread 从 waiting_review 恢复并完成；实际 11 checkpoint rows / 102 write rows |

第一次 TCP 尝试使用端口 8765 时命中一个已有/非本次路由服务，创建接口返回 404，因此没有计为通过；改用独占端口 18765、先校验本次进程存活及 OpenAPI 含 Phase 5 路由后，演示真实通过。上述测试耗时和事件数只作为本机固定 fixture 运行记录，不是性能、准确率或扩展性声明。

### 已知问题

- `InMemoryInvestigationRepository` 不持久化幂等键和历史 SSE 事件。服务可从 checkpoint 重建暂停/完成任务及报告，但重建后的旧事件历史不可重放；生产高可用仍需要持久化任务/事件 Repository。
- PostgreSQL checkpointer 已完成单机 Docker 跨进程验证，但尚未覆盖数据库断线重连、主从切换、连接池压力或多应用实例并发抢占。
- 后台调查使用应用进程内 `asyncio.Task`，没有分布式队列、worker lease、取消 API 或多实例任务抢占；这些不应被描述成生产任务调度系统。
- 默认模型、embedding、Provider 和知识索引仍是确定性 fixture/fake；报告内容不代表真实诊断准确率，Phase 5 没有运行 Evaluation。
- SSE 事件存储当前为进程内无界列表，适合小型演示；生产部署需要事件保留、分页/压缩和慢消费者策略。
- PostgreSQL DSN 是 `SecretStr` 且不会出现在响应/日志，但真实部署仍应通过 secret manager 注入并配置 TLS、最小权限和连接池。

### 手动验证

```text
uv sync
uv run uvicorn incident_copilot.main:app --reload
```

另开终端运行：

```text
uv run python scripts/run_api_demo.py
```

预期脚本输出 `waiting_review` 审核原因、非空 `high_risk_actions`、事件数、不同的 initial/resume run ID，以及最终 `completed`、`probable` 报告和 supporting evidence 数。也可访问 `http://127.0.0.1:8000/docs` 手动检查四个 API。

### 下一阶段建议

只有用户明确要求 Phase 6 后才开始 Evaluation 和 Agent 可观测性。进入前保持 Phase 5 API/事件/报告 Schema 稳定；先设计版本化离线评估样例和可手算 evaluator，不把本阶段 148 项测试通过率、13 条证据或 50 个事件包装成诊断准确率或性能指标。持久化任务/事件 Repository 仍是独立的生产化缺口，不应借 Phase 6 评估代码掩盖。

## Phase 5 环境加固记录

- 2026-07-18：确认 AMD-V/BIOS 虚拟化已开启，Windows Hypervisor 与 VBS 正在运行，Docker Desktop 4.82.0 Linux Engine 可执行容器；先前的 “Virtualization support not detected” 状态已不再存在。
- 使用官方 `pgvector/pgvector:0.8.5-pg18-trixie` 创建 `incident-copilot-postgres`，仅绑定 `127.0.0.1:5432`，持久化到 `incident-copilot-postgres-data`，healthcheck 通过；数据库为 PostgreSQL 18.4，`vector` 扩展为 0.8.5。
- Windows 首次安装官方 saver 时发现纯 `psycopg` 缺少系统 libpq；`postgres` extra 已显式增加 `psycopg[binary] 3.3.4` 并更新锁文件。
- Windows 默认 ProactorEventLoop 不被异步 psycopg 支持；新增 `python -m incident_copilot.server`，使用 `SelectorEventLoop` 承载 Uvicorn/PostgreSQL backend。
- 真实验证中，第一个应用进程将调查暂停为 `waiting_review` 后完全退出；第二个进程用相同 PostgreSQL checkpoint 恢复同一 `thread_id`，接受反馈后进入 `completed`。查询得到 11 条 checkpoint、102 条 checkpoint write。
- 环境加固后的最终门禁：锁文件解析 66 个包，PostgreSQL extra 环境检查 65 个已安装包；Ruff、89 个文件 mypy 和 148 项全量测试通过。Docker client/server 均为 29.6.1，数据库容器状态为 running/healthy。

## Phase 6 — Evaluation 和 Agent 可观测性

### 状态

`completed`

### 完成内容

- 新增 `data/evaluation/incidents-v1.json` 版本化数据集，包含连接池耗尽、DNS 配置错误、cache TTL 回归 3 个不同根因；标签只由 evaluator 消费。
- 新增严格 Evaluation Schema、仓库安全加载器、纯函数 evaluator 和离线 Runner，输出完整报告、逐样例 JSONL、JSON/Markdown 汇总，并保留失败样例。
- 指标覆盖服务定位、故障类型、Recall@K、MRR、工具选择/参数、Evidence relevance、引用正确性、根因准确率、调查轮数、工具次数、wall-clock 时延和 Token；成本无定价时显式 unavailable。
- 新增 checkout/inventory 脱敏 Fixture 与对应 Runbook；仍通过现有 Provider/RAG 契约运行，没有按样例向 Agent 注入答案。
- 节点、工具和结构化模型调用增加默认关闭的 OpenTelemetry spans；关闭时不导入可选包。LangSmith 只在 CLI 显式 `--langsmith` 时启用，默认 Runner 强制关闭 tracing。
- 提交真实基线原始结果和汇总，并在 `docs/EVALUATION.md` 记录指标定义、运行方法、实际数值及不可泛化限制。

### 分步 Git 记录

- `54aa5af`：版本化数据集、三个故障样例、评估器、Runner 与 CLI。
- `ec2332d`：评估边界、禁网、失败保留和原始/汇总输出测试。
- `0c91b44`：默认关闭的节点/工具/模型 OpenTelemetry spans 与可选 extra。
- `8defa0f`：知识语料扩展后的 RAG 规模回归断言。

### 实际检查结果

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv sync` | PASS：默认环境同步完成；可选 observability 依赖不作为默认安装要求 |
| `uv lock --check` | PASS：锁文件与项目元数据一致，解析 69 个包 |
| `uv run ruff format --check .` | PASS：100 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS：All checks passed |
| `uv run mypy src tests scripts` | PASS：100 个 source files，0 issues |
| Phase 6 pytest | PASS：17 passed，0 warning，0.62s |
| 受影响 RAG 回归 | PASS：20 passed，0.31s |
| `uv run pytest` | PASS：165 passed，0 warning，2.71s |
| 离线 Evaluation | PASS：3/3 样例完成，0 失败；生成 JSONL、JSON、Markdown |

耗时仅是本机单次命令记录，不是性能 benchmark。评估基线的平均时延 12.0933 ms、P95 14.9645 ms 也只适用于本次 3 样例固定运行。

### 已知问题

- 数据集只有 3 个同仓脱敏样例，Fake Model 与知识库均为确定性实现；1.0 的服务、故障类型、检索、引用和根因指标不能外推到生产流量。
- 根因准确率使用版本化词法标签而非人工盲审或独立模型 judge；对同义表达和复杂多根因事故覆盖有限。
- Token 为 Fake Model 字符估算，不是供应商 tokenizer 账单；未配置模型定价，因此成本不可用。
- OpenTelemetry 只提供 instrumentation；实际 exporter、采样、collector 和后端由宿主配置。LangSmith 未在默认测试或基线中联网验证。
- Phase 5 的持久化任务/事件 Repository、分布式 worker 等生产化缺口仍未改变，Phase 6 没有掩盖或实现 Phase 7 能力。

### 手动验证

```text
uv sync
uv run python -m scripts.evaluate_offline --output-dir artifacts/evaluation/manual
```

检查 `raw-results.jsonl` 有 3 行、`summary.json` 的完成/失败计数平衡、`summary.md` 明确 estimated Token 与 unavailable cost。

### 下一阶段建议

完成本阶段后停止。只有用户明确要求 Phase 7 才开始真实 Prometheus/Loki/Tempo/OpenTelemetry Demo Adapter、Compose 演示与面试材料；进入前应冻结数据集 `1.0.0`，新增版本而不是修改旧标签。

## Phase 7 — 真实数据源与作品集包装

### 状态

`completed`

### 开始前基线

- 完整重读 `AGENTS.md`、PRD、架构、Graph、数据模型、路线图和进度文档，并检查 Git diff。
- 开始时工作区干净，`HEAD` 与 `origin/main` 均为 Phase 6 完成提交 `6cd3274`。
- Phase 6 已提交离线 Evaluation 原始结果和汇总；Phase 7 没有修改数据集旧标签、调用付费 API 或实现自动修复。

### 完成内容

- 新增 `PrometheusMetricsProvider`，通过标准 Prometheus `/api/v1/query_range` 返回真实 metric Evidence。Adapter 对 base URL、领域指标/聚合 mapping、HTTP timeout、响应字节、序列数、样本数和有限数值设限，保留 source、服务、时间窗、请求 URI、locator 和 SHA-256 citation。
- Prometheus 查询不接受任意 PromQL。当前 mapping 支持 `db.pool.utilization` 与 `http.server.error_rate`；HTTP 400/422、429、超时、不可用、超大/畸形响应统一转换为既有 Provider 错误类别。
- 新增 `metrics_backend=fixture|prometheus` 运行配置。默认保持无网络 Fixture；Prometheus 模式只替换 metrics 端口，日志、Trace、变更、拓扑和 RAG 继续复用现有契约。真实 metrics 失败时 Graph 记录 coverage gap，不暗中伪造 Fixture metrics。
- 新增混合 Provider Graph composition 与集成测试，验证 Prometheus citation 进入最终报告且 Fixture 分支仍可工作。
- 新增 `Dockerfile`、`compose.yaml`、Collector/Prometheus 配置和可选 `demo` 依赖。demo emitter 使用 OpenTelemetry SDK 经 OTLP/HTTP 发送明确标记的 synthetic payment-service 指标；Prometheus 抓取 Collector exporter，Provider 再查询真实时序 API。
- Compose 包含 PostgreSQL/pgvector、OpenTelemetry Collector、Prometheus、指标发生器、FastAPI 和一次性 demo；数据库宿主端口默认 `55432`，镜像使用固定版本，Prometheus 数据保留 2 小时。
- 新增 `scripts/run_observability_demo.py`，等待真实 Prometheus 序列后运行 mixed-source Graph；严格审查后 readiness 要求两个 scrape 周期连续成功，避免健康检查与首批稳定指标之间的冷启动竞态。若链路不可用则非零退出，不回退伪造。新增 fixture 时间平移辅助，使固定脱敏证据与当前演示窗口对齐且保持 Evidence/citation 哈希完整。
- `scripts/run_api_demo.py` 新增 `--live-window`，完整演示创建、SSE、HITL 暂停、接受反馈、完成报告，并统计 Prometheus citation。
- 重写 README 当前状态与快速开始；架构图只显示源码存在的真实 Prometheus Adapter、Fixture 端口、内存 Repository/RAG 和 PostgreSQL saver。新增 `docs/DEMO_GUIDE.md` 与 `docs/INTERVIEW_GUIDE.md`，覆盖项目背景、LangGraph 选择、Graph、RAG、State、循环终止、工具安全、Evaluation、生产缺口、简历描述和面试追问。

### 分步 Git 记录

- `4285c61`：受限 Prometheus metrics Provider 与成功/空结果/参数/超时/失败/畸形响应测试。
- `c672d65`：metrics backend 配置、mixed-source Graph 装配和最终报告 citation 集成测试。
- `9249b8c`：OpenTelemetry Collector/Prometheus/PostgreSQL Compose 栈、指标发生器、真实调查/API 演示和可选依赖。
- `4bdf570`：README、当前实现架构图、演示指南和面试/简历材料。

### 真实容器验收

- 第一次冷构建使用不存在的 uv `0.11.29-python3.13-bookworm-slim` 标签而失败，未计为通过。根据 uv 官方镜像命名修正为 `0.11.29-python3.13-trixie-slim` 后，从头重跑成功。
- `docker compose --profile demo up --build --abort-on-container-exit --exit-code-from demo demo`：PASS，退出码 0；真实路径为 OTLP/HTTP → Collector → Prometheus → Provider → LangGraph；probe 与 Graph 均返回 `ev_prom_*` Evidence，停止原因 `evidence_sufficient`，本次调用 7 个工具、报告有 13 个 citation。
- `docker compose up -d --build api`：PASS；API、PostgreSQL、Prometheus 为 healthy，Collector 与 emitter 为 running。`/health` 返回 `ok`。
- `uv run python scripts/run_api_demo.py --live-window`：PASS；一次调查经历 `waiting_review → accept → completed`，初始/恢复 run ID 不同，本次读取 47 个 SSE 事件、报告有 10 条 supporting evidence 和 1 条 Prometheus citation。
- `docker compose --profile demo down -v --remove-orphans`：PASS；清理后该 Compose 项目无容器残留。未删除此前独立运行的本机 PostgreSQL 容器。

上述工具数、citation 数和 SSE 事件数只是本机单次功能记录，不是性能、准确率、吞吐或扩展性指标。

### 最终质量门禁

| 命令/检查 | 真实结果 |
| --- | --- |
| `uv sync` | PASS：默认离线环境同步成功；移除可选 demo/postgres 包后基础功能仍可安装 |
| `uv lock --check` | PASS：锁文件与项目元数据一致，解析 74 个包 |
| `uv run ruff format --check .` | PASS：107 个 Python 文件已格式化 |
| `uv run ruff check .` | PASS：All checks passed |
| `uv run mypy src tests` | PASS：98 个 source files，0 issues |
| `uv run --extra demo mypy scripts` | PASS：9 个 source files，0 issues |
| Phase 7 pytest | PASS：29 passed，0 warning，0.95s |
| `uv run pytest` | PASS：184 passed，0 warning，2.90s |
| `uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md` | PASS：文档 Mermaid 与当前编译 Graph 一致 |
| `docker compose --profile demo config --quiet` | PASS：Compose 配置解析通过 |

测试耗时是本机单次运行记录，不是 benchmark。

### 新增或修改文件

- Provider/装配：`src/incident_copilot/tools/providers/prometheus.py`、`tools/providers/__init__.py`、`tools/__init__.py`、`core/config.py`、`graph/bootstrap.py`、`graph/__init__.py`、`main.py`、`.env.example`。
- 演示运行：`src/incident_copilot/demo.py`、`scripts/emit_demo_metrics.py`、`scripts/run_observability_demo.py`、`scripts/run_api_demo.py`、`Makefile`。
- 容器：`Dockerfile`、`.dockerignore`、`compose.yaml`、`deploy/otel-collector-config.yaml`、`deploy/prometheus.yml`。
- 测试：`tests/unit/tools/test_prometheus_provider.py`、`tests/unit/test_demo_scripts.py`、`tests/unit/core/test_config.py`、`tests/integration/test_prometheus_graph.py`。
- 依赖/文档：`pyproject.toml`、`uv.lock`、`README.md`、`docs/ARCHITECTURE.md`、`docs/DEMO_GUIDE.md`、`docs/INTERVIEW_GUIDE.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`。

### 已知问题

- 真实接入只覆盖 Prometheus metrics。Loki、Tempo、真实变更/拓扑系统和官方 OpenTelemetry Demo 的业务指标语义 mapping 未实现；当前 emitter 是仓库内 synthetic demo signal。
- 默认 Model 和 embedding 仍是 Fake；作品集演示证明控制流和证据链，不证明真实 LLM 诊断能力。
- Investigation/SSE Repository 仍在内存，后台任务仍是进程内 `asyncio.Task`；PostgreSQL saver 不能代替持久化任务/事件仓储和分布式 worker。
- pgvector Adapter 已实现契约但默认 Compose RAG 仍使用内存向量索引；没有 Alembic 管理的业务表、外部 Evidence Store 或真实知识增量同步。
- Compose 凭据和 synthetic emitter 只适合 localhost 演示；没有鉴权、租户隔离、TLS、secret manager、数据库 HA、压力/恢复测试。
- Evaluation 仍只有三个同仓样例，结果不能外推生产准确率。未执行新的性能 benchmark，也未声称 P95、吞吐或成本改善。

### 手动验证

完全离线：

```text
uv sync
uv run pytest
uv run python scripts/run_investigation.py
```

真实 metrics 证据链：

```text
docker compose --profile demo up --build --abort-on-container-exit --exit-code-from demo demo
docker compose --profile demo down -v --remove-orphans
```

完整 API/HITL：

```text
docker compose up -d --build api
uv run python scripts/run_api_demo.py --live-window
docker compose --profile demo down -v --remove-orphans
```

详细预期与排错见 `docs/DEMO_GUIDE.md`。

### 下一阶段建议

路线图定义的 Phase 0–7 已全部完成，本次在 Phase 7 停止，不自动开启新阶段。如果后续单独立项生产化，优先级建议为：持久化 Investigation/Event Repository 与 Evidence Store；接入真实 LLM/embedding 并扩展人工审阅评估集；再增加 Loki/Tempo Adapter、鉴权/租户隔离和分布式 worker。任何新增工作都应保持默认 Fixture 回归和真实结果披露纪律。
