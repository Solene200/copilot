# 文档真实性与一致性审查

## 范围与结论

已对 README、PRD、ARCHITECTURE、GRAPH_DESIGN、GRAPH_CURRENT、DATA_MODEL、ROADMAP、PROGRESS、DEMO_GUIDE、INTERVIEW_GUIDE、部署/可观测性文档、学习分章、源码精读分章及两个聚合指南进行源码对照。`GRAPH_CURRENT.md` 的自动拓扑校验通过；整体文档质量高于一般作品集。

主要问题是“历史、当前、目标”没有始终分开：根目录 `AGENTS.md` 仍把项目锁在 Phase 4，而 README/PROGRESS 声称 Phase 7；ARCHITECTURE 的 Evidence Repository、jitter、rerank/context compression 被写得像当前能力；面试指南继承了 Graph 重试这一实际上不成立的说法。学习指南生成门禁当前直接失败，两个巨型聚合文件已不是可重建产物。

## 声明—实现对照

| 文档声明 | 实际源码/运行 | 判定 | 建议措辞 |
|---|---|---|---|
| `AGENTS.md`：当前完成 Phase 4，未经确认不得 Phase 5 | README/PROGRESS/源码已实现 API、RAG、评估等后续阶段 | 错误 | 将仓库治理基线更新为真实当前 Phase，或取消阶段数字改用 capability gates |
| PRD：从自然语言解析服务、时间与症状 | API 强制调用方给 services/start/end；parse 节点只校验 | 夸大 | “接收已结构化事故范围；raw_query 供规划参考” |
| PRD/演示：支持证据、反证、被拒假设可查看 | schema 存在，默认链只产 supporting，报告不填 rejected | 夸大 | 标为“模型边界已预留，默认 provider 尚未演示” |
| README/INTERVIEW：七类工具共享有限 retry | Registry 有 retry；Graph 传 remaining=1，运行时不 retry | 错误 | 修复前改为“Registry 支持 retry，当前 Graph 每 step 单 attempt” |
| ARCHITECTURE：大 payload 写 Evidence Repository/object store | 当前没有 Evidence Repository，完整 Evidence 转 Ref 后丢弃 | 目标被写成现状 | 分成 Current Architecture 与 Target Architecture |
| ARCHITECTURE：指数 backoff + jitter | 有指数 backoff，无 jitter；Graph 路径不触发 | 错误 | “Registry 实现无 jitter backoff；Graph 接入待修复” |
| ARCHITECTURE：optional rerank/context compression | 源码无 reranker/语义压缩 | 仅设计 | 明确放 Future/Optional，不放当前查询链 |
| README：PgVectorStore adapter 已实现 | 类和 SQL 合同测试存在，默认未接且本次无真实 DB | 基本真实但需限定 | “experimental adapter；recording contract tested, live DB unverified” |
| PROGRESS/DEMO：历史 compose/Prometheus/Postgres 验证成功 | 记录可能真实；本次只验证 compose config | 无法独立复核 | 加日期、commit、命令和 artifact；不要让历史记录等同当前验收 |
| README：无 API Key 可运行 | CLI/API/RAG/eval 本次均实跑通过 | 真实 | 保留 |
| README：SSE/HITL/same-thread resume | API Demo 与测试通过 | 真实 | 保留 |
| GRAPH_CURRENT：当前节点和路由 | render check 通过 | 真实 | 保留为自动生成事实来源 |
| 评估文档：citation correctness | 实现只测 Ref/report 自洽，fixture hash 可为占位 | 指标名称误导 | 拆为 reference consistency、resolver success、content integrity |

## 文档生成门禁失败

以导入 `scripts/build_learning_guide.py` 并调用纯构建函数的方式进行只读校验，`_validate_source_coverage()` 抛出：

```text
源码精读覆盖检查失败
缺少源码精读链接: src/incident_copilot/core/clock.py
```

因此当前 `INCIDENT_COPILOT_LEARNING_GUIDE.md` 与 `INCIDENT_COPILOT_SOURCE_CODE_GUIDE.md` 不能从分章和源码成功重建。常规 pytest、ruff、mypy 均未包含这一门禁。这是明确的 P1 文档交付失败，而非格式偏好。

## 结构与重复

两个聚合指南合计超过 5,000 行非空文本，内容又来自学习分章和 code-walkthrough 分章。仓库同时用 README、PRD、ARCHITECTURE、ROADMAP、PROGRESS、DEMO、INTERVIEW 和聚合指南描述“当前能力”，造成同一事实有太多维护点。

推荐单一事实来源：

- README：当前可运行能力、5 分钟 quickstart、明确限制。
- ROADMAP：未来工作与验收，不记录完成叙事。
- PROGRESS：按 commit/date 记录真实命令与结果。
- ARCHITECTURE：Current 与 Target 分栏。
- GRAPH_CURRENT：自动生成当前图。
- 学习分章：解释机制；聚合版作为 CI/release artifact，不重复提交。
- INTERVIEW_GUIDE：只引用上述事实，不独立声明新能力。

## 文档问题

### IC-P1-06：仓库治理说明与实际阶段冲突

- 证据：`AGENTS.md` 明写 Phase 4/禁止 Phase 5；README、PROGRESS 与源码已经越过该阶段。
- 影响：后续开发者/Agent 会被错误门禁阻止，或被迫忽略仓库最高优先级规则。
- 修复：一次性校准阶段；更推荐改为能力 gate，避免数字持续漂移。
- 验收：AGENTS、README、ROADMAP、PROGRESS 对当前能力无冲突。

### IC-P1-07：学习文档无法重建

- 证据：`scripts/build_learning_guide.py:_validate_source_coverage()` 缺 `core/clock.py`。
- 影响：已提交聚合文档可能陈旧，所谓“源码全覆盖”不成立。
- 修复：增加 clock 精读/链接；在 CI 临时目录生成并 diff，不污染工作树。
- 验收：纯构建成功、生成结果与跟踪文件一致，且新增任意 `src/incident_copilot/**/*.py` 会使覆盖门禁失败。

### IC-P2-08：历史验证与当前可验证状态混写

- 影响：读者会把某次 compose 成功理解为当前 commit、当前机器、当前配置已通过。
- 修复：每个运行记录附 date/commit/environment/artifact；能力矩阵单独标“本次验证/历史验证/未验证”。

### IC-P2-09：架构目标被写成当前实现

- 影响：Evidence Store、jitter、rerank、压缩等能力被面试者误述。
- 修复：ARCHITECTURE 每节使用 Current / Target / Decision 三段，删除不存在的当前目录树节点。

## 文档结论

文档“多且认真”，但还不是“单一事实来源”。完成 P0/P1 修复时应同步做一次删减：少写一份能力宣言，多保留一份可执行验收记录。审查报告不建议现在重写原文档，因为本任务只允许最终审查输出。
