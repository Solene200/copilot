# 00 学习路线

## 学习目标

完成这套文档后, 你应能回答四个问题:

1. 一次 HTTP 请求如何变成可恢复的 LangGraph 调查。
2. 并行工具结果为什么不会互相覆盖。
3. 模型为什么不能决定无限循环或执行任意工具。
4. 报告中的 Evidence ID 和 Citation 如何一路保留下来。

## 三条阅读路线

### 路线 A: 先跑起来

适合第一次接触仓库的读者。

```text
01 项目介绍
→ 02 目录关系
→ 12 本地运行
→ 03 请求生命周期
→ 14 术语表
```

完成标志: 能启动 API、运行离线调查, 并知道输出来自 Fixture/Fake Model 而非在线服务。

### 路线 B: 学 LangGraph 工程化

```text
03 请求生命周期
→ 04 State 与 Reducer
→ 05 Graph 与 Node
→ 08 调查循环与假设
→ 10 Checkpoint 与 HITL
→ graph/state.py、builder.py、nodes.py walkthrough
```

完成标志: 能解释 `Send`、superstep、reducer、conditional edge、interrupt 和 `thread_id`。

### 路线 C: 学 AI 后端边界

```text
06 Provider 与 Tool
→ 07 RAG
→ 09 FastAPI 与异步任务
→ 11 Evaluation 与测试
→ tools/registry.py、rag/retrieval.py、evaluation/runner.py walkthrough
```

完成标志: 能解释输入校验、超时重试、引用保留、离线评估和标签隔离。

## 推荐实践节奏

每读完一个专题, 按以下顺序练习:

1. 找到文档列出的真实文件和函数。
2. 先读函数 docstring, 再看对应 walkthrough。
3. 运行该模块的定向测试。
4. 修改一个 Fixture 参数并观察测试为什么失败。
5. 不要一开始就接真实 LLM; 先保持控制流可复现。

## 关键检查点

| 阶段 | 你应该能解释 | 对应测试 |
| --- | --- | --- |
| 请求入口 | 202 为什么不是完成 | `test_investigation_api_phase5.py` |
| State | 并行计数为何返回增量 | `test_reducers.py` |
| Graph | 7 个工具如何真正并行 | `test_investigation_graph.py` |
| Tool | Provider 越界结果为何被拒绝 | `test_registry.py` |
| RAG | BM25 和向量分数为何不直接相加 | `test_retrieval.py` |
| HITL | 重复恢复为什么返回冲突 | `test_investigation_service.py` |
| Evaluation | ground truth 为什么不进入 Graph | `test_offline_evaluation.py` |

## 不建议的阅读方式

- 不要只读 `nodes.py` 而跳过 `state.py` 和 `routing.py`。
- 不要把 `FakeModelProvider` 的规则输出理解为真实模型能力。
- 不要把 PostgreSQL Checkpointer 理解为完整任务持久化。
- 不要把测试通过数当作诊断准确率。

下一步: [项目整体介绍](01-project-introduction.md)。
