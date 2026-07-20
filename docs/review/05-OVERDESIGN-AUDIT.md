# 过度设计与复杂度审查

## 结论

仓库没有系统性“为抽象而抽象”。大多数 Protocol、状态字段和 LangGraph 机制都有第二实现、测试替身或明确运行需求。问题主要集中在两处：同一学习内容同时维护分章与两个超大生成文件，形成高维护噪声；`PgVectorStore` 在尚未进入真实运行链前已经承担较多实现和说明成本。

更重要的是，项目当前的风险不是代码太多，而是少数核心语义太薄：Evidence 哈希真实性、多场景 planning、反证链和 Graph retry 都比新增抽象重要。

## 抽象使用审查表

| 抽象/模块 | 路径 | 实际使用情况 | 判断 | 建议 |
|---|---|---|---|---|
| 七类 Tool Protocol | `tools/interfaces.py` | fixture、RAG、Prometheus 多实现；Registry 与测试广泛依赖 | 保留 | 这是清晰替换边界，不合并成万能 `Tool` |
| Tool Registry | `tools/registry.py` | 参数、防护、超时、错误、重试集中执行 | 保留 | 修正 Graph 预算传递，不拆更多 middleware |
| ModelProvider | `graph/model.py` | Graph 完全依赖协议，当前仅一个 fake 实现 | 保留但降调宣传 | 接真实模型前无需再造 ModelFactory 层级 |
| InvestigationRepository Protocol | `application/repository.py` | 当前只有内存实现；service 全面依赖 | 保留 | 它隔离生命周期和未来持久化，成本可控 |
| checkpoint 工厂 | `graph/checkpoint.py` | 内存默认，Postgres 可选 | 保留 | 不把 checkpoint 当业务 repository |
| VectorStore Protocol | `rag/vector_store.py` | 内存与 pgvector 两实现 | 保留 | 真实数据库验证前标为 experimental |
| `PgVectorStore` | `rag/pgvector_store.py` | 仅 recording fake 合同测试，未接默认 app | 简化或完成二选一 | 短期不集成就从主路径/主宣传移出；否则补容器测试和 migration |
| 大型 `InvestigationState` | `graph/state.py` | 所有字段参与跨节点生命周期，且有界 | 保留 | 不拆成多个嵌套 state；只澄清 attempt 统计 |
| 单体 `GraphNodes` | `graph/nodes.py` | 10 个节点和共用 helper 集中，约 700+ 行 | 暂时保留 | 只有真实 Provider 引入后，再抽 model-call policy/report assembler |
| 领域模型层 | `domain/` | API、Graph、Provider、评估共享，含有效不变量 | 保留 | 补“内容哈希复算”这一真正缺失的不变量 |
| 事件投影模型 | `application/events.py` 等 | SSE 和调查状态真实使用 | 保留 | 不再引入 event bus，除非多进程/外部消费者成为需求 |
| 评估 schema/evaluator/runner 分层 | `evaluation/` | CLI、tests、JSON artifact 都使用 | 保留 | 扩数据集和盲测优先于再抽 evaluator plugin 框架 |
| 两份聚合学习指南 | `docs/learning/INCIDENT_*_GUIDE.md` | 由分章自动生成，合计超过 5,000 行非空内容 | 明显冗余 | 仓库只跟踪分章；聚合版在 release/CI artifact 生成，或只保留一份 |
| 学习文档生成器 | `scripts/build_learning_guide.py` | 校验源码覆盖、生成两份巨型文档；当前校验已失败 | 有价值但职责过重 | 保留覆盖检查，简化展示性 emoji/重写逻辑，生成产物不重复入库 |
| 运行时 phase 命名 | graph/checkpoint/config 相关 | 历史阶段写入标识，文档已进入 Phase 7 | 删除历史耦合 | 用稳定名称 + schema/version，不让开发计划进入业务标识 |
| `model_api_key` 配置 | `config.py` | 未被任何真实 ModelProvider 使用 | 删除或延期 | 接真实 provider 的同一 Phase 再增加 |

## 为什么 LangGraph 不是过度设计

如果主线只是一次性函数调用，LangGraph 确实会多余；本仓库却真实需要 fan-out/barrier、条件研究循环、可暂停人工审核、同线程恢复和可观察中间事件。每个机制都有运行证据和测试，换成手写状态机会得到同等甚至更高复杂度。

不建议继续增加：supervisor、多 agent 协商、子图层级、动态工具注册市场、长期记忆或自主执行 remediation。它们没有当前验收需求，会稀释已经很清楚的控制流。

## 文档复杂度

分章文档本身服务于学习目标，合理；同时提交两份完整聚合版本会带来：

- diff 中大量重复内容，审阅信噪比低；
- 源码变更必须同时依赖生成器覆盖规则；
- 当前 `core/clock.py` 新增后，生成器 `_validate_source_coverage()` 已失败，但常规 pytest/ruff/mypy 未发现；
- 架构、PRD、README、PROGRESS、ROADMAP、面试指南又分别复述能力状态，容易产生 Phase 4/Phase 7 冲突。

建议建立一个“事实来源矩阵”：ROADMAP 只放计划与验收，PROGRESS 只放真实历史，README 只放当前可运行能力，ARCHITECTURE 明确区分 current/target，学习指南从源码/章节生成但不作为能力状态来源。

## 可删除、简化与延期

### 现在可简化

- 不再版本化两份聚合学习文档，或至少只保留一份。
- 删除运行时名称中的 Phase 标签。
- 移除未使用 `model_api_key`，直到真实 ModelProvider Phase。

### 必须保留

- Tool Protocol/Registry、ModelProvider、VectorStore Protocol。
- EvidenceRef 与有界 Graph State。
- `Send`、checkpoint、HITL 和事件投影。
- 评估结果中的失败样本与运行 metadata。

### 明确延期

- 多 agent/supervisor、自动修复执行、云向量库抽象层、模型路由平台。
- 生产鉴权/队列/多租户，直到部署目标从本地作品集变为共享服务。
- reranker，直到有更大、独立的检索数据集能证明收益。

## 复杂度投资优先级

下一份新增代码首先应减少错误，而不是增加能力：1）canonical Evidence hash + resolver；2）基于 incident 的场景化 planning；3）Graph attempt 预算；4）反证 fixture。完成这四项后，再决定 pgvector、真实 LLM 和生产存储是否值得投入。
