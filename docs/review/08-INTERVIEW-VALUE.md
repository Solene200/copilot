# 简历与面试价值评估

## 最终判断

项目有明显面试价值，尤其适合 AI 应用工程、LangGraph、Python 后端和平台工程岗位。它最强的不是“模型很聪明”，而是把不可信模型、只读工具、证据来源、预算、并行控制、HITL、checkpoint 和 API 生命周期放进一套可解释的工程边界。

当前可以现场演示，但不建议在修复 P0 前把“可验证 citation 完整性”写成卖点，也不建议把 3 个同仓库 fixture 的 1.0 指标写成模型准确率。简历可用度：7.5/10；修复 P0、场景化 planning、Graph retry 和反证链后可到 9/10。

## 最值得讲的能力

1. **可恢复 LangGraph 控制流**：`Send` 并行取证、barrier 聚合、条件研究循环、`interrupt`/`Command` 人审、同 thread resume 都有真实源码和测试。
2. **确定性离线优先**：没有 API Key 也能完成 CLI、API、RAG、评估与 Demo；fixture 和真实 Provider 共享端口。
3. **安全边界**：工具只读、参数和预算有界、模型输出强校验、风险 remediation 不自动执行。
4. **可观测应用层**：调查/run/thread/request ID 分离，真实 graph updates 投影为可重放 SSE。
5. **可复现评估**：失败样本、运行 metadata、估算 token 与无价格成本都明确记录，没有虚构生产指标。

## 面试时必须主动限定

- 默认 ModelProvider 是确定性 fake，不是已接真实 LLM。
- 三样本评估是 regression harness，不是盲测 benchmark。
- FakeEmbedding 是可复现基线，不证明语义检索质量。
- pgvector/Postgres/Prometheus 有适配器或历史记录，但本次审查未做 live stack 复核。
- 自然语言 parse、多服务、反证链和 Evidence resolver 尚不完整。
- 当前 citation correctness 指标只代表对象一致性；修复 P0 前不能称内容完整性验证。

主动说清这些限制不会降低项目价值，反而证明候选人能区分“软件路径正确”和“AI 效果有效”。

## 推荐简历表述

修复 P0/P1 前，可使用以下限定版本：

> 构建可离线复现的 IncidentCopilot：使用 LangGraph `Send` 并行编排七类只读证据工具，以条件路由实现有界研究循环，并用 checkpoint、`interrupt`/`Command` 支持同线程人工审核恢复；通过 FastAPI/SSE 暴露真实中间事件，196 个测试覆盖控制流、预算、降级、RAG 与 API 生命周期。

> 实现 BM25 + 确定性向量检索 + RRF 的混合知识检索和可替换 Provider 边界；构建 3 个脱敏 fixture 的离线回归评估，明确区分估算 token、无价格成本和非生产 benchmark。

不要写：

- “根因识别准确率 100%”；
- “引用真实性 100%”；
- “生产级 pgvector/Postgres/Prometheus 已验证”；
- “支持任意事故自然语言自主调查”；
- “多 Agent 系统”（当前是单 Graph 工作流，不是多 agent）。

## 推荐 Demo 顺序

1. 先运行质量门禁或展示 196 tests，不需要逐个讲。
2. 启动 API，创建 payment incident。
3. 观察 SSE 中 plan、七类 tool、evidence、hypothesis、budget 事件。
4. 在 high-risk remediation 处展示 interrupt；解释为什么不自动执行生产变更。
5. resume 后对比同 thread ID 与新 run ID。
6. 打开 `GRAPH_CURRENT.md` 对照 `Send` fan-out 和条件路由。
7. 运行一次 RAG search，展示 BM25/vector/RRF 和 citation。
8. 最后主动展示评估限制，而不是只展示 1.0 指标。

修复 P0 前，Demo 不应把 content hash 当作亮点；可以把发现并修正评估 oracle 作为工程复盘案例。

## 高频面试问题与可信回答

### 为什么用 LangGraph，不用普通 async 函数？

因为需求同时包含并行 fan-out/barrier、条件循环、可暂停外部人审和 checkpoint resume。若没有后两项，普通 async pipeline 会更简单；这里 LangGraph 的状态和恢复语义有直接价值。

### 如何避免模型编造 Evidence ID？

模型只看到有界 EvidenceRef；`verify_hypotheses()` 会把 supporting/contradicting ID 与 state 中实际 evidence IDs 求交，且 probable 报告要求至少两个独立来源。需要补充：当前默认 provider 没有真正演示反证链。

### 并行工具如何保证确定性？

每个 step 有稳定 ID，state reducer 按 ID 合并，aggregate 是 fan-out barrier，测试用不同延迟证明完成顺序不改变最终结果。

### checkpoint 为什么不能代替业务数据库？

checkpoint 保存 Graph 执行状态，不负责调查列表、run history、完整 SSE 和 Evidence payload。当前恢复最小记录正好展示这两个职责必须分离。

### 评估 1.0 说明什么？

只说明这三个同仓库、脱敏、确定性样本的回归链路能复现，不说明泛化。citation 指标当前还只测内部一致性，已经列为 P0 修复。

### 为什么先用 FakeEmbedding/FakeModel？

为了无 Key、无网络、可复现地测试控制流和工程契约。替换边界的价值只有在接真实实现后才完全证明，因此不能把 fake 的结果包装成模型质量。

### 当前最严重的 bug 是什么？

Evidence 契约声明 hash 是内容 SHA-256，但部分 fixture 用重复字符占位；evaluator 又只比较同源对象，所以错误得到 1.0。它说明强类型不能代替语义 oracle。

## 能力信号

| 岗位方向 | 信号强度 | 原因 |
|---|---:|---|
| LangGraph / Agent 工程 | 强 | 控制流机制真实且有恢复/HITL |
| Python 后端 | 强 | 分层、Pydantic、async、SSE、错误与生命周期完整 |
| RAG 工程 | 中上 | hybrid/过滤/增量真实；缺真实 embedding/rerank/DB 证据 |
| AI 评估 | 中 | pipeline 好，数据与 oracle 仍弱 |
| SRE/可观测性 | 中 | 领域建模好；live 观测源只覆盖窄场景 |
| 生产平台 | 中下 | 无持久业务库、鉴权、队列、多租户 |

## 达到简历最终版的最小条件

不需要先接付费模型。只要完成以下四项，作品集说服力会显著提升：

1. 所有 Evidence 真实复算 hash，citation resolver 与评估拆分三层完整性指标；
2. 三个 fixture 分别生成场景化计划，不再固定 payment 查询；
3. Graph retry 与全局 attempt 预算一致；
4. 一个 fixture 真实展示竞争假设、反证和 rejected hypothesis。

完成后再重新跑全门禁、API Demo 和评估，并只报告真实新结果。
