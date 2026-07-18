# IncidentCopilot

IncidentCopilot 是一个面向 AI 应用开发岗位面试与作品集展示的多源可观测性故障诊断项目。当前仓库已完成 Phase 4：在 Provider、工具和 RAG 之上，提供有界、可复现的 LangGraph 并行调查循环与带 Evidence ID 引用的最终报告。

当前阶段不包含调查 HTTP API、SSE、checkpoint、人工审核、数据库部署、真实可观测平台或在线模型调用；这些不应从现有 `/health` API 推断为已实现。

## 环境要求

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/)

Docker 不是 Phase 2–4 离线运行与测试的前提。

## 快速开始

```text
uv sync
uv run uvicorn incident_copilot.main:app --reload
```

服务启动后访问：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

`/health` 不访问数据库、网络或任何付费 API。

## Phase 2 工具

默认 payment-service fixture 位于 `data/incidents/payment-service-pool-exhaustion.json`，包含内部一致的日志、指标、Trace、变更、拓扑、Runbook、历史事故以及 evaluation-only ground truth。可用工具为：

- `search_logs`
- `query_metrics`
- `query_traces`
- `get_service_topology`
- `get_recent_changes`
- `search_runbooks`
- `search_similar_incidents`

工具输入使用严格 Pydantic Schema；Registry 统一处理 allow-list、deadline、timeout、有限重试、调用预算、错误归一化和结构化日志。所有 fixture 工具测试均离线运行；Phase 3 的 `RagKnowledgeProvider` 在不修改工具名的前提下把两个知识工具接到 Hybrid Search。

## Phase 3 RAG

知识库使用带 TOML frontmatter 的 Markdown，当前包含 Runbook、payment-service 服务说明和历史事故。默认链路完全离线：

```text
DocumentLoader → heading-aware splitter → Fake Embedding + BM25
               → in-memory vector search → RRF → content-hash dedupe
```

初始化并验证重复 ingest 的稳定计数：

```text
uv run python scripts/ingest_knowledge.py
```

执行带 query rewrite、metadata filter 和 citation 的检索演示：

```text
uv run python scripts/search_knowledge.py --query "database connection pool timeout" --service payment-service --top-k 3
```

限制到 Runbook：

```text
uv run python scripts/search_knowledge.py --query "连接池超时" --service payment-service --document-type runbook
```

Fake Embedding 只用于验证确定性管线，不代表在线 embedding 的语义质量。`PgVectorStore` 使用参数化 SQL、embedding 模型/版本隔离和事务式文档替换，默认测试通过 recording session 验证契约；Adapter 不在运行时建表，真实部署必须由 Alembic migration 预置 schema。当前机器未运行真实 PostgreSQL/pgvector 集成测试。

检索结果中的 `score` 是归一化 RRF 排序分数，不是根因概率或置信度。

## Phase 4 LangGraph 调查

运行完整 payment-service 离线调查：

```text
uv run python scripts/run_investigation.py
```

默认装配使用 Fixture Provider、Phase 3 Hybrid RAG 和 Fake Model，不需要 API Key 或网络。Graph 通过动态 `Send` 并行收集证据，按并发上限分批执行全部计划步骤，并使用稳定 ID reducer 汇合；研究轮数、真实 Provider 尝试、模型调用、估算 Token 和 deadline 均有代码预算。结构化模型输出经 Pydantic 校验，模型超时、运行异常或连续无效时有限重试并转入明确规则降级。

当前源码图位于 [docs/GRAPH_CURRENT.md](docs/GRAPH_CURRENT.md)。检查文档是否与编译图一致：

```text
uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md
```

Fake Model 只验证控制流、结构化边界和可复现演示；假设文本从高相关 Evidence summary 派生，不读取 evaluation ground truth，但其规划配方仍只面向 payment-service 场景，不代表真实模型诊断准确率。报告 disposition、confidence 和估算 Token 也不是 Evaluation 结果。

## 质量检查

```text
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run mypy src tests
uv run pytest
```

## 配置

复制 `.env.example` 为 `.env` 后按需修改。所有环境变量使用 `INCIDENT_COPILOT_` 前缀。模型密钥是可选项，默认运行和测试不需要设置。

## 文档

- [产品需求](docs/PRD.md)
- [总体架构](docs/ARCHITECTURE.md)
- [Graph 设计](docs/GRAPH_DESIGN.md)
- [当前源码 Graph](docs/GRAPH_CURRENT.md)
- [数据模型](docs/DATA_MODEL.md)
- [路线图](docs/ROADMAP.md)
- [进度](docs/PROGRESS.md)
