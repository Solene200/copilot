# 测试与评估可信度审查

## 实际质量门禁

审查基线全部常规门禁通过：

| 命令 | 结果 |
|---|---|
| `uv lock --check` | 通过，74 packages |
| `uv run ruff format --check .` | 通过，110 files |
| `uv run ruff check .` | 通过 |
| `uv run mypy src tests scripts` | 通过，110 source files |
| `uv run pytest` | **196 passed in 3.37s** |
| `uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md` | 通过 |
| `docker compose config` | 通过 |

测试默认不访问付费 API；评估测试也显式防止网络。没有发现为了过测试而删除断言、吞异常或绕开主路径。

## 测试质量判断

测试不是只有 schema happy path。高价值覆盖包括：

- Graph 完整运行、研究循环、预算停止、并行 barrier 与 reducer 确定性；
- malformed model output 的有限重试和 Provider 部分失败降级；
- 工具 allowlist、时间窗、结果数、超时、retryable/non-retryable；
- FastAPI 创建、状态、SSE、`Last-Event-ID`、HITL resume、同 thread 恢复；
- fixture 与真实 Prometheus adapter 的 fake-transport 合同；
- RAG 切分、幂等 ingest、BM25/vector/RRF、filter、pgvector SQL 合同；
- 评估 runner 的成功/失败样本、指标计算、禁止网络和产物序列化。

这种覆盖足以支撑“工程链路稳定”的结论。它不等同于“AI 效果可靠”，因为测试数据与默认规则由同一仓库共同设计。

## 离线评估实跑

本次将 artifact 写到仓库外临时目录，3 个样本全部完成：

| 指标 | 实际值 | 严格解释 |
|---|---:|---|
| service localization | 1.0 | 输入 fixture 已明确给出 service，不证明自然语言定位 |
| failure type accuracy | 1.0 | 小型、同仓库标签集 |
| retrieval recall@k | 1.0 | 6 文档知识库上的回归结果 |
| retrieval MRR | 1.0 | 同上 |
| tool selection F1 | 0.9487 | 工具名称大多通用，但参数跨场景不正确 |
| tool argument accuracy | 0.7857 | 暴露固定 payment 计划，但平均值仍显得较高 |
| evidence relevance F1 | 0.7852 | payment 样本 supporting evidence 过宽，F1 仅 0.5556 |
| citation correctness | 1.0 | 只验证 report citation 与 EvidenceRef 相同，不验证原始内容/hash |
| root cause accuracy | 1.0 | 词法匹配 + 同仓库 fixture，不是模型泛化指标 |
| mean rounds/tools | 1 / 7 | 默认计划固定且一轮即充分 |
| token usage | 估算 | FakeModel 按序列化长度估算，不是供应商计费 token |
| cost | 无 | 未配置价格，不能推导成本 |

checkout 与 inventory 样本的 `_plan()` 仍查询 database acquisition、`db.pool.utilization`、`POST /payments`。它们之所以最终 root cause 正确，主要是通用 change/topology 步骤与知识检索命中了正确 fixture，随后词法规则从证据摘要产生根因。这个结果适合回归，不适合声称“跨场景 agent planning 已验证”。

## P0：citation 指标不可作为完整性证明

`domain/evidence.py:Citation.content_hash` 注释和 `Evidence.content_hash` 契约都声明为完整内容的 SHA-256；validator 只检查格式与 Citation/Evidence 两字段相等。`data/incidents/checkout-service-dns-misconfiguration.json` 明确使用 `1111…`、`2222…`、`3333…` 等占位 hash，inventory fixture 也使用重复字符占位值。

`evaluation/evaluators.py:citation_metrics()` 比较的是 report 中 citation 与 EvidenceRef 中 citation 的 URI、locator、hash。由于两者本就来自同一个对象投影，即使 hash 与内容完全无关也得 1.0。这属于测试 oracle 错误，而不是低覆盖率。

按项目 payment fixture 实际采用的 canonical JSON 方式（key 排序、紧凑 separators、UTF-8）独立复算：payment 为 12/12 匹配，checkout 为 0/5，inventory 为 0/5，example 为 0/1。这既证明 canonical 规则可从现有有效数据确认，也证明另外 11 条不是仅有“可疑外观”，而是确定不匹配。

修复后应至少有三层断言：

1. 对 `Evidence.content` 定义 canonical JSON/text serialization 并复算 SHA-256；
2. resolver 按 citation URI/locator 重新取得不可变来源/快照并复算；
3. evaluator 分开报告“引用存在”“定位可解析”“内容哈希匹配”，不能合并成一个自洽分数。

## 关键缺失测试

| 优先级 | 缺失场景 | 为什么重要 | 建议验收测试 |
|---|---|---|---|
| P0 | fixture content/hash 真实性 | 当前 1.0 citation 指标可被占位 hash 欺骗 | 逐条解析 fixture、canonical hash、resolver round-trip |
| P1 | Graph 路径 retry | Registry 单测通过但节点固定 remaining=1 | 首次 retryable、第二次成功，断言 attempts/预算/stats |
| P1 | 三类事故的场景化 plan | 现有 root cause 指标掩盖错误 tool args | 对每个 fixture 断言 query/metric/operation 与症状一致 |
| P1 | 竞争假设、反证、淘汰 | 核心解释性字段从未进入默认报告 | 至少一例支持 A、反驳 B，并输出 rejected reason |
| P1 | 多服务语义 | 目前只调查第一个却报告全部 | 服务顺序互换不改变正确结果；无证据服务不得列 affected |
| P2 | 真实 Postgres checkpoint | 当前只有装配/历史记录 | 容器中 pause、进程重建、resume、事件/状态一致 |
| P2 | 真实 pgvector | recording fake 不验证 SQL 方言/索引 | migration、upsert、filter、distance、dimension error 集成测试 |
| P2 | live Prometheus | fake transport 不验证合成 metrics 与 PromQL | compose profile 冒烟，产生指标后得到 Prom citation |
| P2 | 文档生成门禁 | 当前 `core/clock.py` 漏读未被 CI 捕获 | CI 只读/临时生成并 diff 两个聚合文档 |
| P3 | API auth/limit/idempotency | 共享部署前置 | 未授权、跨租户、重复 create、突发并发测试 |

## 聚合指标的分母

`evaluation/evaluators.py:aggregate_evaluation()` 对质量指标只聚合 `SampleStatus.COMPLETED` 样本，失败数另列。这个实现本身合理，但文档中“失败保留在汇总分母”的说法容易让人误以为失败会拉低所有质量均值。应明确：sample/failure rate 包含失败；质量均值仅含 completed。更严格的总分可额外定义 failure-as-zero，但不要悄悄改变现有指标。

## 测试结论

工程测试质量为 8.5/10；AI 评估可信度为 5.5/10。当前最错误的做法是继续增加测试数量或追求 coverage 百分比。应先修复 oracle：真实 hash、场景化计划、反证链和 Graph retry 这四类测试，每一类都比几十个 schema 单测更有价值。
