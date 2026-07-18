# IncidentCopilot Offline Evaluation

- Run: `evalrun_20260718T085033Z_58a018e0`
- Dataset: `dataset_incident_copilot_offline` version `1.0.0`
- Samples: 3 (3 completed, 0 failed)
- Raw results: `raw-results.jsonl`

| Metric | Value |
| --- | ---: |
| Service localization accuracy | 1.0000 |
| Failure type accuracy | 1.0000 |
| Retrieval Recall@K | 1.0000 |
| Retrieval MRR | 1.0000 |
| Tool selection F1 | 0.9487 |
| Tool argument accuracy | 0.7857 |
| Evidence relevance F1 | 0.7852 |
| Citation correctness | 1.0000 |
| Root-cause accuracy | 1.0000 |
| Mean research rounds | 1.0000 |
| Mean tool calls | 7.0000 |
| Mean latency (ms) | 12.0933 |
| P95 latency (ms) | 14.9645 |
| Total tokens | 12353 |
| Mean tokens | 4117.6667 |
| Token usage estimated | True |
| Estimated cost | N/A (no pricing configured) |

## Limitations

- This is a fixture regression evaluation, not a production generalization claim.
- Latency is single-process wall-clock time on the current machine, not a benchmark.
- Fake Model token counts are deterministic character-based estimates.
- Root-cause accuracy uses versioned lexical indicators, not an LLM-as-judge.
- Cost is unavailable because no provider pricing was configured.
