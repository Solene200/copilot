# IncidentCopilot 中文学习中心

这套文档面向已经理解 LangGraph 基本概念、但还不熟悉完整 AI 后端工程的读者。文档解释的是当前仓库真实实现, 不是理想化架构。

实现基线为 `26f6130`。阶段三只新增教学文档; 从该提交到教学文档提交之间没有源码、Graph 或测试变更。

## 推荐入口

1. [学习路线](00-learning-path.md)
2. [项目整体介绍](01-project-introduction.md)
3. [目录和模块关系](02-directory-and-modules.md)
4. [一次请求的完整生命周期](03-request-lifecycle.md)
5. [State 和 Reducer](04-state-and-reducers.md)
6. [Graph、Node、Edge 与调查循环](05-graph-and-nodes.md)
7. [Provider、Tool 和 Evidence](06-providers-and-tools.md)
8. [RAG 索引和检索](07-rag-pipeline.md)
9. [调查循环与假设](08-investigation-loop-and-hypotheses.md)
10. [FastAPI 与异步任务](09-fastapi-and-async.md)
11. [Checkpoint、Streaming 与 HITL](10-checkpoint-streaming-hitl.md)
12. [Evaluation 与测试](11-evaluation-and-tests.md)
13. [本地运行和演示](12-local-run-and-demo.md)
14. [常见问题和面试问答](13-faq-and-interview.md)
15. [术语表](14-glossary.md)

需要直接读代码时, 使用[核心源码阅读索引](core-reading-index.md)和[逐文件源码解读](code-walkthrough/README.md)。

## 阅读约定

- “State 读取/写入”专指 LangGraph `InvestigationState`。
- “任务状态”专指 `InvestigationRecord.status`。
- “Checkpoint”保存 Graph 执行快照, 不等于业务 Repository。
- “真实 Prometheus”表示真实经过 HTTP API 的指标链路; 其他数据源仍可能来自 Fixture。
- 文档中的流程图只展示源码中存在的节点、函数和依赖。

## 当前生产化边界

当前实现没有真实 LLM、持久化 Investigation/Event Repository、外部 Evidence Store、Loki/Tempo Adapter、分布式 worker 或自动修复能力。教学文档会明确这些边界, 不把作品集实现描述成完整生产系统。
