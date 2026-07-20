# RAG 专项审查

## 结论

当前 RAG 不是把字符串列表包装成“向量检索”。它实现了文档加载、确定性切分、metadata、FakeEmbedding、内存向量相似度、BM25、RRF 融合、过滤、稳定 citation 与增量摄取，并通过 `RagKnowledgeProvider` 接入两类知识工具。本次实际 ingest 6 个文档得到 18 个 chunks，重复 ingest 数量不变；查询 `database connection pool timeout` 同时出现 BM25/vector 命中。

它仍是本地确定性检索基线，而不是生产级 RAG：没有真实 embedding、rerank、语义上下文压缩和已验证的 pgvector 链路；查询改写表偏向 payment/database-pool；返回结果允许同一 document 的多个 chunk 占据前列。

## 加载、切分与元数据

- `rag/loader.py` 读取 Markdown/frontmatter，校验文档类型、服务、标签和来源。
- `rag/splitter.py` 按标题/段落与大小边界切分，生成稳定 chunk ID。
- `rag/schemas.py` 为 document、chunk、query、hit、citation 提供 Pydantic v2 约束。
- 原文档 ID 和内容哈希参与幂等更新；同内容重复 ingest 不增加记录。

边界清楚：loader/splitter 不知道 Graph，Graph 不直接读取文件。Fixture 目录可替换为其他 loader。

## 向量检索与 BM25

`FakeEmbedding` 是确定性、无网络的测试/演示 embedding，保证可复现，但不应称为语义质量实现。`InMemoryVectorStore` 真实计算相似度并支持 filters；BM25 真实计算词项统计。两者不是返回预设答案的 stub。

局限是默认查询改写和 embedding 都与小型英文知识库共同设计。它们验证软件路径，不验证开放语料上的召回能力。

## Hybrid、过滤与去重

`rag/retriever.py:HybridRetriever` 对 BM25 和 vector 排名做 reciprocal-rank fusion，并应用 service/document-type/tags 等过滤。评估指标在 document 层去重，避免同一文档多个 chunk 重复抬高 recall/MRR，这是正确的。

运行时结果列表本身不做 document-level diversity：相邻 chunk 可能来自同一文档。对需要上下文连续性的问答这不一定错误，但在事故调查的有限 evidence 槽位里会挤占来源多样性。建议在 top-k 最终阶段增加“每文档最多 N 个 chunk”或 MMR，而不是把所有重复 chunk 粗暴去掉。

## 查询改写、rerank 与压缩

- 查询改写：`rag/query_rewrite.py` 的确定性别名扩展已实现，可解释、可测试；但词表明显围绕连接池和支付场景，状态为部分实现。
- rerank：源码没有 reranker，状态为未实现。架构文档提到“可选 rerank”必须明确属于未来设计。
- 上下文压缩：当前只有摘要/长度截断和有界 `ModelContext`，不是基于查询的压缩，状态为未实现。

现阶段优先级不应是立即接 cross-encoder。先修复场景化 query 生成和文档多样性，收益更直接；数据规模扩大后再引入可选 reranker Protocol。

## 增量更新

`rag/ingestion.py` 按稳定 ID/upsert 写入，重复摄取保持 6 documents / 18 chunks。本地进程内的增量行为成立。需要限定：

- 内存 store 重启后丢失；
- 文档相同但 metadata 改变时的更新语义要由 ID/hash 规则明确；
- 当前没有 tombstone/删除源文档同步策略；
- `PgVectorStore` 的增量行为只经 recording fake 合同测试，本次未连接真实数据库。

## Citation 设计

知识检索的 `internal://knowledge/...` URI、chunk locator 和内容哈希由摄取过程生成，比手写事故 fixture 可靠。`RagKnowledgeProvider` 将 hit 转成 Evidence 后，Graph 只保存 EvidenceRef。

仍缺统一 resolver：用户无法通过 citation API 重新读取“采集时的原文片段”并复算 hash。未来 Evidence Store 应保存不可变 payload 或快照，citation resolver 应按 URI/locator 取回并验证内容，而不是仅比较两个对象里的同一个字符串。

## PgVectorStore

`rag/pgvector_store.py:PgVectorStore` 有参数化 SQL、向量维度检查、filter 组装与合同测试，不是空类；但它没有进入默认 app 装配，也没有本次真实 Postgres/pgvector migration 与查询验证。因此状态是部分实现。

从作品集范围看，这个适配器处在“有一定价值但交付证据不足”的边缘。若短期不做真实集成，应把它从主能力清单降为 optional adapter；若保留为亮点，则补容器集成测试、迁移所有权、索引策略与 EXPLAIN 记录。继续扩展它但不接运行链才是过度设计。

## RAG 问题清单

### IC-P2-03：结果缺少文档多样性约束

- 影响：有限 top-k 被同文档相邻 chunks 占用，独立来源数下降。
- 复现：检索结果允许同 document ID 多次出现；评估器需要额外去重才得到 document 指标。
- 修复：最终融合后按 document cap 或 MMR 选择；保留相邻 chunk 合并选项。
- 验收：top-k 的独立文档数达到配置下限，同时不降低现有三样本 recall/MRR。

### IC-P2-04：查询改写表过度场景化

- 影响：DNS/cache 等故障的知识查询仍可能被连接池词汇牵引。
- 修复：从 Incident + symptoms + 当前证据动态构造可验证 query，规则表只作同义词归一。
- 验收：checkout、inventory、payment 三个 fixture 分别生成不同且与 ground truth 无关泄漏的 query。

### IC-P2-05：pgvector 交付状态容易被过度解读

- 影响：面试者可能把 recording fake 合同测试描述为真实数据库验证。
- 修复：选择“补真实集成”或“降级为实验性适配器”之一。
- 验收：文档、默认配置、测试证据对同一状态使用一致措辞。
