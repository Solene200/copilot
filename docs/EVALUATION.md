# IncidentCopilot 离线评估

## 运行方式

默认命令不访问网络，不需要模型 API Key，使用 Fixture Provider、确定性 Fake Embedding 和 Fake Model：

```text
uv run python -m scripts.evaluate_offline --output-dir artifacts/evaluation/latest
```

输出包括：

- `raw-results.jsonl`：每个样例的预测、检索排序、工具名与参数、Evidence/Citation 指标、完整报告、轮数、调用数、实测时延和 Token 来源。
- `summary.json`：机器可读汇总。
- `summary.md`：人工可读汇总。

Runner 会捕获单个样例异常并写入原始结果，失败样例仍计入 `sample_count` 和 `failed_sample_count`，不会静默丢弃。

## 数据集

当前数据集为 `dataset_incident_copilot_offline` 版本 `1.0.0`，定义在 `data/evaluation/incidents-v1.json`，包含 3 个脱敏故障：

1. payment-service 数据库连接池上限被错误下调。
2. checkout-service DNS resolver 配置错误。
3. inventory-service cache TTL 被设为零。

每个样例引用独立 Fixture，并标注受影响服务、故障类型、根因关键词、相关 Evidence/知识文档、理想工具和关键参数。Ground truth 只在 Graph 完成后交给 evaluator；Runner 传给 Graph 的只有 `IncidentContext`、Fixture Provider 和固定预算。

这只是小型 fixture 回归集，不是独立同分布的生产数据集，也不能用来证明模型泛化能力。

## 指标定义

| 指标 | 定义 |
| --- | --- |
| 服务定位 | 预测与标签服务集合的 exact match；原始结果同时保留 precision/recall/F1 |
| 故障类型 | 透明、样例无关的词法 taxonomy 分类结果与标签 exact match |
| Recall@K | 前 K 个去重 document ID 覆盖相关文档的比例；无相关标签时定义为 1 |
| MRR | 前 K 个结果中第一个相关 document ID 的倒数排名；未命中为 0 |
| 工具选择 | 期望与实际工具名集合的 F1 |
| 工具参数 | 仅比较 ground truth 明确标注的字段；运行时额外预算字段不受罚 |
| Evidence relevance | 报告 supporting Evidence ID 与相关 Evidence ID 集合的 F1 |
| 引用正确性 | 每个 EvidenceRef 的 citation ID、URI、locator、content hash 是否与报告 Citation 完全一致 |
| 根因准确率 | 报告覆盖至少 75% 版本化根因关键词记为正确；同时保留连续 term recall |
| 轮数/工具次数 | 直接读取最终报告的实测调查统计 |
| 时延 | Runner 使用 `perf_counter` 测量单进程 Graph wall-clock；P95 使用样本线性插值 |
| Token | 读取 ModelProvider usage；Fake Model 的字符估算明确标记 `estimated=true` |
| 成本 | 未配置模型与定价时为 `unavailable_no_pricing`，不推算或伪造 |

集合 evaluator 的两个空集合定义为 perfect exact match；期望非空而实际为空时 precision/recall/F1 均为 0。Citation 没有可检查 Evidence 时返回 `null`，聚合时不把未定义分母当作满分。

## Phase 6 实际基线

提交产物位于 `artifacts/evaluation/phase6-baseline/`。本次运行 ID 为 `evalrun_20260718T085033Z_58a018e0`，3/3 样例完成、0 个失败：

| 指标 | 实际值 |
| --- | ---: |
| 服务定位准确率 | 1.0000 |
| 故障类型准确率 | 1.0000 |
| Retrieval Recall@K | 1.0000 |
| Retrieval MRR | 1.0000 |
| 工具选择 F1 | 0.9487 |
| 工具参数准确率 | 0.7857 |
| Evidence relevance F1 | 0.7852 |
| 引用正确性 | 1.0000 |
| 根因准确率 | 1.0000 |
| 平均调查轮数 | 1.0000 |
| 平均工具调用数 | 7.0000 |
| 平均时延 | 12.0933 ms |
| P95 时延 | 14.9645 ms |
| 总 Token | 12,353（estimated） |
| 平均 Token | 4,117.6667（estimated） |
| 估算成本 | N/A（未配置定价） |

这些数值只描述 2026-07-18 在当前 Windows/Python 3.13 机器上的一次固定 fixture 运行。样例少且与 Fake Model/知识库同仓，1.0 指标不能解释为生产准确率；时延也不是稳定 benchmark。工具参数与 Evidence relevance 的非满分结果被原样保留。

## 可观测性

OpenTelemetry 默认关闭且不会导入可选包。节点、工具和结构化模型调用分别使用 `incident_copilot.node.*`、`incident_copilot.tool.execute`、`incident_copilot.model.structured_complete` span 名称。启用方式：

```text
uv sync --extra observability
$env:INCIDENT_COPILOT_OTEL_ENABLED="true"
```

应用宿主仍需配置自己的 TracerProvider/SpanProcessor/exporter；项目默认不选择外部后端，也不会自动发送数据。

LangSmith 使用 LangGraph 原生 tracing，必须显式传入 `--langsmith`；默认 Runner 会通过 tracing context 覆盖外部环境变量并关闭 tracing：

```text
$env:LANGSMITH_API_KEY="<your-key>"
uv run python -m scripts.evaluate_offline --langsmith --langsmith-project incident-copilot-eval
```

LangSmith 是可选外部服务，不属于默认测试或本次离线基线。以上命令没有在线模型调用，但会把 Graph trace 发送到配置的 LangSmith workspace。
