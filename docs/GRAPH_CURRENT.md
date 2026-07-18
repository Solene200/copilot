# Phase 5 当前源码 Graph

下图由启用 `require_human_review=True` 的实际离线图通过 `draw_mermaid()` 直接生成，
对应当前工作区中的实际节点与边。虚线为条件边；从计划/细化节点到 `collect_evidence`
的条件边在运行时返回多个 `Send`，并行分支在 `aggregate_evidence` 汇合。若仍有
待执行步骤，聚合节点按并发上限发送下一批；deadline 或硬预算停止可直接转报告。

`generate_report` 仅在报告包含 high/critical 修复建议时路由到 `human_review`。
该节点执行真实 `interrupt()`；接受反馈后结束，追加调查反馈则回到
`refine_investigation`。Checkpoint、后台任务与 SSE 属于图外应用层，不会伪画成 Graph 节点。

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	parse_incident(parse_incident)
	build_investigation_plan(build_investigation_plan)
	collect_evidence(collect_evidence)
	aggregate_evidence(aggregate_evidence)
	generate_hypotheses(generate_hypotheses)
	verify_hypotheses(verify_hypotheses)
	judge_evidence(judge_evidence)
	refine_investigation(refine_investigation)
	generate_report(generate_report)
	human_review(human_review)
	__end__([<p>__end__</p>]):::last
	__start__ --> parse_incident;
	aggregate_evidence -.-> collect_evidence;
	aggregate_evidence -.-> generate_hypotheses;
	aggregate_evidence -.-> generate_report;
	build_investigation_plan -.-> aggregate_evidence;
	build_investigation_plan -.-> collect_evidence;
	build_investigation_plan -.-> generate_report;
	collect_evidence --> aggregate_evidence;
	generate_hypotheses --> verify_hypotheses;
	generate_report -.-> __end__;
	generate_report -.-> human_review;
	human_review -.-> __end__;
	human_review -.-> refine_investigation;
	judge_evidence -.-> generate_report;
	judge_evidence -.-> refine_investigation;
	parse_incident -.-> build_investigation_plan;
	parse_incident -.-> generate_report;
	refine_investigation -.-> aggregate_evidence;
	refine_investigation -.-> collect_evidence;
	refine_investigation -.-> generate_report;
	verify_hypotheses --> judge_evidence;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

一致性检查：

```text
uv run python scripts/render_graph.py --check docs/GRAPH_CURRENT.md
```
