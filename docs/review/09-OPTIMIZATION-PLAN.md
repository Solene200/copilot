# 优化计划与规范化问题台账

## 执行原则

先修真实性，再修泛化，再清理文档，最后才考虑生产化。禁止用放宽断言、删除 fixture、改指标名称掩盖现有失败。每个批次都应重新运行锁文件、格式、lint、mypy、全量 pytest、Graph 文档检查、CLI/API Demo、RAG 与离线评估，并记录真实结果。

## P0：必须立即修复

### IC-P0-01：Evidence 内容哈希为占位值，citation 评估仍给 1.0

- **问题描述**：领域注释声明 `Citation.content_hash` / `Evidence.content_hash` 是完整内容 SHA-256；validator 只验证 64 位 hex 和两个字段相等。checkout fixture 使用 `111…` 至 `555…`，inventory 与 example 也使用不匹配值。`citation_metrics()` 只比较 report citation 与 EvidenceRef 的同源字符串，无法发现假 hash。
- **受影响模块**：`domain/evidence.py:Citation`、`Evidence.validate_time_range()`；`data/incidents/checkout-service-dns-misconfiguration.json`、`inventory-service-cache-regression.json`；`evaluation/evaluators.py:citation_metrics()`；所有 fixture provider 与报告 citation 声明。
- **用户影响**：报告看似提供可验证内容完整性，实际无法证明引用内容未被替换；评估产出 1.0 会误导用户、面试官和后续开发。
- **为什么重要**：证据可信度是项目核心价值。错误 oracle 比无测试更危险，因为它给出虚假通过信号；符合 P0“citation/评估不可相信”的定义。
- **复现方法**：按 payment fixture 已采用的 key 排序、紧凑 separators、UTF-8 canonical JSON 独立复算，payment 12/12 匹配，而 checkout 0/5、inventory 0/5、example 0/1 匹配。运行离线评估仍得到 citation correctness 1.0；`citation_metrics()` 没有读取 `Evidence.content` 或 URI。
- **修复建议**：定义版本化 canonical hashing（JSON 使用 UTF-8、排序 key、固定 separators；文本使用规范化规则）；Evidence 创建时统一计算而非手填；增加不可变 Evidence Store/fixture resolver；把指标拆为 reference consistency、locator resolvability、content integrity。
- **可验收条件**：所有 fixture 逐条复算匹配；篡改 content、hash、locator 任一项都有失败测试；resolver round-trip 成功；旧的占位 hash 数据迁移或拒绝加载；评估分别输出三层指标且不再把内部自洽叫“citation correctness”。

## P1：简历最终版前必须修复

### IC-P1-01：默认计划器对所有事故硬编码 payment/database-pool 查询

- **问题描述**：`graph/model.py:FakeModelProvider._plan()` 不根据 symptoms/failure type 生成首轮计划，固定使用 `connection acquisition`、`db.pool.utilization`、`POST /payments`、`connection pool timeout`。
- **受影响模块**：`graph/model.py`、`graph/schemas.py:ModelContext`、三套 incident fixtures、evaluation tool argument 指标。
- **用户影响**：checkout DNS 和 inventory cache 事故执行错误查询；最终根因碰巧由 change/topology/知识片段命中，掩盖规划失败。
- **为什么重要**：planning 是 Copilot 的核心 AI 行为；当前 1.0 root-cause 不能证明它成立。
- **复现方法**：分别运行三个 evaluation sample，检查 `actual_tool_plan`；后两者仍出现 payment/db 参数，tool argument accuracy 只有约 0.64/0.71。
- **修复建议**：以 incident raw query、symptoms、service 和已有 evidence 生成不同确定性计划；fake 可用规则矩阵，但不得读取 ground truth。真实模型未来复用同一 schema。
- **可验收条件**：DNS、cache、pool 三类计划参数各自正确；交换 fixture 名称不改变基于内容的计划；ground truth 在 Graph 完成前不可访问；新增集成断言覆盖 query/metric/operation。

### IC-P1-02：Graph 运行路径关闭了工具重试且 attempt 统计不一致

- **问题描述**：`GraphNodes.collect_evidence()` 传 `remaining_tool_calls=1`，`ToolRegistry.execute()` 的 `max_attempts=min(max_retries+1, remaining)` 因此永远为 1；节点统计又按 step 而非 `result.attempts` 加一。
- **受影响模块**：`graph/nodes.py`、`tools/registry.py`、`graph/state.py`、InvestigationStats、预算/错误测试。
- **用户影响**：瞬时 Provider 失败不会按文档重试；恢复重试后又可能低估预算和报告统计。
- **为什么重要**：这是声明与真实控制流直接冲突，也影响失败恢复与成本边界。
- **复现方法**：在 Graph 集成路径注入首次 retryable、第二次成功的 Provider；实际只调用一次并进入 degraded error。
- **修复建议**：定义“物理 attempt”预算，fan-out 前原子预留；QueryContext 传真实剩余额度；stats 同时记录 logical steps 与 attempts；并发分支不得各自透支全局余额。
- **可验收条件**：retryable 成功路径 attempts=2；non-retryable=1；全局上限在并发下不超；报告 stats、event 与 checkpoint 恢复后一致。

### IC-P1-03：PRD 声明的自然语言事故解析未实现

- **问题描述**：API 强制 services/start_time/end_time，`GraphNodes.parse_incident()` 只校验结构化 Incident，不从 raw query 提取服务、时间和症状。
- **受影响模块**：`api/schemas.py`、`api/routes/investigations.py`、`graph/nodes.py:parse_incident()`、PRD/Demo 文档。
- **用户影响**：用户必须预先完成定位，产品入口能力低于声明。
- **为什么重要**：它决定产品边界。要么实现，要么诚实收窄契约，不能模糊。
- **复现方法**：尝试只提交 raw query；请求在进入 Graph 前因缺字段失败。
- **修复建议**：两个合理选项：A）实现结构化 parser，并为不确定字段进入 clarification；B）把产品明确定位为“结构化 incident investigator”，删除自然语言解析声明。
- **可验收条件**：选 A 时，raw-query-only fixture 可得到经校验字段且不猜测不确定时间；选 B 时，所有文档/API 名称统一且无“自动解析”承诺。

### IC-P1-04：反证与 rejected hypothesis 没有默认运行闭环

- **问题描述**：领域 schema 有 contradicting evidence 和 rejected hypotheses，但 `FakeModelProvider._hypotheses()` 只给 supporting IDs，`generate_report()` 不构造 rejected_hypotheses。
- **受影响模块**：`graph/model.py`、`graph/nodes.py`、`domain/hypothesis.py`、`domain/report.py`、Demo 与评估。
- **用户影响**：系统只会证明首个解释，不能展示如何排除竞争根因。
- **为什么重要**：反证能力是事故诊断区别于摘要生成的关键。
- **复现方法**：运行默认 CLI/API Demo；contradicting evidence 与 rejected hypotheses 均为空。
- **修复建议**：增加至少一个包含竞争假设的 fixture；模型输出证据支持与反证；verify 过滤伪造 ID；报告 assembler 保存被拒原因与引用。
- **可验收条件**：Demo 可见至少两个假设、一条反证和一个 rejected hypothesis；删除反证后判定会变化；所有 ID 可追溯真实 Evidence。

### IC-P1-05：多服务输入只调查第一个，却把全部服务写入报告

- **问题描述**：`GraphNodes._model_context()` 选择 `incident.services[0]`，`generate_report()` 使用整个 services 元组作为 affected_services。
- **受影响模块**：`graph/nodes.py`、`graph/schemas.py:ModelContext`、tool planning、report assembly、API schema。
- **用户影响**：未调查服务可能被错误标记受影响；服务顺序可能改变调查内容。
- **为什么重要**：这是直接的报告正确性问题。
- **复现方法**：提交两个服务并交换顺序；计划只针对首项，报告仍列两项。
- **修复建议**：短期把 API 限制为单服务；或正式按服务 `Send` 规划/取证，affected services 从证据与假设推导。
- **可验收条件**：输入顺序不影响结论；无证据服务不出现在 affected；预算按服务 fan-out 仍有界。

### IC-P1-06：`AGENTS.md` 阶段门禁与仓库实际状态冲突

- **问题描述**：治理文件声明完成 Phase 4 且禁止 Phase 5，README/PROGRESS/源码却已到 Phase 7 能力。
- **受影响模块**：`AGENTS.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`、README、后续 Agent 工作流。
- **用户影响**：维护者收到互相冲突的最高优先级规则，可能停止合法工作或错误忽略治理文件。
- **为什么重要**：仓库自描述直接影响未来变更安全性。
- **复现方法**：对照 `AGENTS.md` 第 24 行与 README/PROGRESS 当前阶段。
- **修复建议**：校准为真实阶段；更稳妥的是用能力 gate 代替持续漂移的 Phase 数字。
- **可验收条件**：治理、路线、进度和 README 对当前/下一步无冲突，新增 Phase 需要单一位置更新。

### IC-P1-07：学习文档生成器已无法通过自身源码覆盖门禁

- **问题描述**：`scripts/build_learning_guide.py:_validate_source_coverage()` 报 `src/incident_copilot/core/clock.py` 缺少精读链接，两个已提交聚合指南不能重建。
- **受影响模块**：学习分章、code-walkthrough、两个 `INCIDENT_*_GUIDE.md`、CI/文档脚本。
- **用户影响**：读者得到可能陈旧的“全源码”指南，维护者无法安全再生成。
- **为什么重要**：项目把教学文档作为核心交付，生成失败属于实际功能失败。
- **复现方法**：导入脚本并调用 `build_learning_guide()` 或运行脚本；覆盖校验立即抛错。
- **修复建议**：补 clock 章节/链接；CI 在临时目录生成并 diff；减少跟踪重复聚合产物。
- **可验收条件**：两种 build 函数成功，产物与跟踪版本一致；新增源码文件没有精读覆盖时 CI 失败。

## P2：建议在下一轮质量改进处理

### IC-P2-01：模型输出顺序被当作 hypothesis 排名

- **描述/模块**：`GraphNodes.generate_report()` 使用第一项作为 leading，缺显式 rank；影响真实 Provider 多假设稳定性。
- **复现**：交换两个等价 Hypothesis 的返回顺序，观察 root cause 改变。
- **修复/验收**：按 status、confidence、独立来源数和稳定 ID 排序；交换顺序不得改变结果。

### IC-P2-02：运行时 graph 名称包含过期 Phase 标签

- **描述/模块**：builder/checkpoint 可观测标识与 Phase 7 文档不一致；影响 trace 和面试解释。
- **复现**：查看编译 graph name/事件 metadata。
- **修复/验收**：改稳定业务名 + schema version；历史阶段不再进入运行标识。

### IC-P2-03：Hybrid top-k 缺文档多样性

- **描述/模块**：`rag/retriever.py` 可返回同一文档多个 chunk，挤占独立来源；评估器只能事后按 doc ID 去重。
- **复现**：查看 top-k 中重复 document ID。
- **修复/验收**：配置 per-document cap/MMR；独立来源数提高且现有 recall/MRR 不下降。

### IC-P2-04：查询改写规则偏 payment/database-pool

- **描述/模块**：`rag/query_rewrite.py` 的别名表对 DNS/cache 覆盖弱；影响跨场景召回。
- **复现**：对三类症状比较 rewrite，观察共享连接池词汇。
- **修复/验收**：由 incident/symptoms/evidence 构造 query，规则只归一同义词；三类输出应不同且不读 ground truth。

### IC-P2-05：PgVectorStore 的交付证据不足

- **描述/模块**：`rag/pgvector_store.py` 有实现但仅 recording fake 合同测试；用户可能误解为 live 验证。
- **复现**：默认 app 不装配它，本次无真实 DB 命令结果。
- **修复/验收**：补 migration + 容器集成 + filter/distance/index 测试，或文档降为 experimental adapter。

### IC-P2-06：checkpoint 恢复不包含完整业务记录和事件

- **描述/模块**：`InvestigationService._recover_from_checkpoint()` 只重建最小记录；历史 SSE/run 不可恢复。
- **复现**：清空内存 repository 后从 checkpoint 查询。
- **修复/验收**：目标若要求跨重启，新增持久 Investigation/Run/Event repository；重启后完整历史一致。

### IC-P2-07：外部 `X-Request-ID` 未限长和规范化

- **描述/模块**：API middleware/log context 可能接收换行或超长值；本地影响低，公网风险上升。
- **复现**：发送超长/控制字符 header，观察日志上下文。
- **修复/验收**：服务端 canonical ID，外部 ID 限长限字符；结构化日志无注入。

### IC-P2-08：历史运行记录与本次可验证状态没有统一标签

- **描述/模块**：PROGRESS/DEMO 中过去的 compose 成功容易被当作当前已验证；影响可信陈述。
- **复现**：对比历史文档与本审查只完成 `docker compose config` 的证据。
- **修复/验收**：所有记录附 date/commit/environment/artifact，能力矩阵区分 current/historical/unverified。

### IC-P2-09：ARCHITECTURE 把目标能力写成当前能力

- **描述/模块**：Evidence Repository、jitter、rerank、context compression 和不存在目录项混入现状描述。
- **复现**：逐项在源码搜索实现；相关实现不存在或不完整。
- **修复/验收**：每节明确 Current/Target/Decision；自动检查文档引用路径存在。

### IC-P2-10：质量均值只含 completed，文档“失败在分母”措辞含糊

- **描述/模块**：`aggregate_evaluation()` 的质量均值过滤 failed；failure_count 另列。用户可能误认为失败会降低所有均值。
- **复现**：构造 1 completed + 1 failed，质量均值等于 completed 样本。
- **修复/验收**：文档明确各指标分母；可额外提供 failure-as-zero 总分但保留原始指标。

### IC-P2-11：真实 Prometheus metric 映射仍是支付场景窄切片

- **描述/模块**：Prometheus provider 的允许 metric/PromQL 主要支撑 payment demo，checkout/inventory 期望指标未形成等价 live 路径。
- **复现**：尝试用 evaluation 中 DNS/cache 的期望 metric 走真实 adapter mapping。
- **修复/验收**：要么明确 live demo 只支持 payment，要么为三类场景提供受控映射和 compose 冒烟证据。

## P3：明确延期，除非部署目标变化

### IC-P3-01：生产调查存储、鉴权、限流与多 worker

- **描述/影响**：当前内存 Repository、进程内任务/锁、无 auth，不可公网或横向扩展。
- **复现**：重启/多 worker 后记录不共享；匿名请求可访问 API。
- **建议/验收**：只有在共享部署需求确定后引入数据库 repository、认证授权、租户、分布式协调与压测 SLO。

### IC-P3-02：真实 LLM 与真实 embedding Provider

- **描述/影响**：只有 FakeModel/FakeEmbedding；不能宣称真实模型质量，但不妨碍离线控制流。
- **复现**：搜索 `ModelProvider` concrete implementation 和 `model_api_key` 使用，无真实 adapter。
- **建议/验收**：在独立 Phase 接入至少一个可选 Provider，默认测试继续 fake；结构化输出、超时、重试、预算和无 Key fallback 全部覆盖。

### IC-P3-03：扩大盲测数据与生产可观测源

- **描述/影响**：3 个同仓库样本不足以估计泛化、性能和成本。
- **复现**：evaluation dataset 仅三条，ground truth 与规则在同仓库。
- **建议/验收**：版本化 train/dev/blind split，引入未参与规则设计的脱敏事故；报告置信区间、失败率、硬件与真实 token/cost 元数据。

### IC-P3-04：清理未使用配置与避免预置未来抽象

- **描述/影响**：`model_api_key` 当前无消费者，容易暗示能力存在。
- **复现**：配置字段存在但源码无使用路径。
- **建议/验收**：删除到真实 Provider Phase，或实现后用配置装配；未使用配置由测试/静态规则发现。

## 推荐实施批次

### 批次 A：可信证据门禁（必须先完成）

范围只含 IC-P0-01。建立 canonical hash、fixture 迁移、resolver 和三层 citation 指标。完成前不更新简历指标。

### 批次 B：核心调查正确性

依次完成 IC-P1-01、IC-P1-05、IC-P1-04。先让三类场景的 plan 正确，再处理多服务契约和反证报告，避免用更多 fixture 掩盖默认规则偏置。

### 批次 C：运行可靠性

完成 IC-P1-02，统一 attempt、budget、events、stats 与 checkpoint 语义。并发预算必须有集成测试，不只测 Registry。

### 批次 D：声明校准

对 IC-P1-03 做产品选择；完成 IC-P1-06/07 和 P2 文档项。删除重复聚合文档或改为 release artifact。

### 批次 E：可选增强

先用数据证明需要，再做 RAG diversity、pgvector/live Prometheus。P3 项不属于“修完当前版本”的必选条件。

## 每批次统一验收命令

```text
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests scripts
uv run pytest
uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md
uv run python scripts/build_learning_guide.py
uv run python scripts/run_investigation.py
uv run python scripts/ingest_knowledge.py
uv run python scripts/search_knowledge.py "database connection pool timeout"
uv run python scripts/evaluate_offline.py --dataset data/evaluation/incidents-v1.json --output-dir <temp-dir>
docker compose config
```

API/HITL 批次还必须启动 server 后执行 `scripts/run_api_demo.py`。涉及 Postgres/Prometheus 的批次必须提供本次 commit 的 live compose artifact；否则继续标为无法验证。

## 停止线

达到简历最终版的最小停止线：P0 为零；IC-P1-01/02/04/05 全部关闭；IC-P1-03 有明确实现或范围决策；文档生成门禁恢复；全量命令真实通过；评估重新生成且不复用本次分数。无需为达到该停止线提前实施 P3。
