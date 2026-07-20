# 功能与业务闭环审查

## 结论

默认 payment-service 场景已经形成真实闭环，不是 Mock HTTP 外壳：`create_investigation()` 创建调查，`InvestigationService._run_graph()` 消费 LangGraph 更新，工具返回 Evidence，假设和报告经 Pydantic 输出，风险修复动作触发 HITL，resume 在同一 thread 上继续。实际 API Demo 从创建到完成收到 50 个 SSE 事件，最终报告为 `probable`，有 13 条 supporting evidence。

闭环的主要边界是“已经结构化的单服务事故 + 默认支付连接池模式”。若按 PRD 更宽的描述理解为“用户只给自然语言，系统自动定位服务/时间/症状，并能在多类事故中提出正反假设”，则只实现了一部分。

## 主用户流程核验

1. `api/routes/investigations.py:create_investigation()` 校验 `CreateInvestigationRequest`，生成独立 incident/investigation/thread/run ID。
2. `application/investigation_service.py:InvestigationService.create()` 写入内存记录并启动后台 Graph。
3. `graph/nodes.py:parse_incident()` 做服务、时间和 deadline 防御性检查；它不解析自然语言。
4. `FakeModelProvider._plan()` 返回七类工具步骤，`graph/builder.py:_dispatch_batch()` 用 `Send` 并行分发。
5. `GraphNodes.collect_evidence()` 通过 `ToolRegistry.execute()` 调 Provider，完整 Evidence 随即投影为 EvidenceRef。
6. `aggregate_evidence()` 在 fan-out barrier 后聚合；`generate_hypotheses()`、`verify_hypotheses()`、`judge_evidence()` 完成推理门禁。
7. 不充分时进入 `refine_investigation()`；充分或预算耗尽时进入 `generate_report()`。
8. 风险动作存在时 `human_review()` 调 `interrupt()`；API `resume_investigation()` 用新 run ID 和原 thread ID 继续。
9. `InvestigationService._project_update()` 将节点更新转成可重放 SSE 事件；最终 `GET /investigations/{id}` 返回报告。

## 业务正确性

### 已证明成立

- 稳定 ID：同一调查的 investigation/thread ID 稳定，resume 生成新 run ID。
- 时间范围：请求与工具参数经 timezone-aware Pydantic 模型和 Registry 二次约束。
- 只读边界：七个工具都是查询型端口，修复动作只进入报告和人工审批，不会执行生产变更。
- 局部失败降级：单 Provider 失败会记录结构化错误和限制项，其余来源仍可完成报告。
- 停止条件：充分证据、轮次、工具数、模型调用数、deadline 都能终止研究。
- 不充分时降级：无法满足支持证据/来源数量门槛时报告为 `inconclusive`，不会强行给 probable。

### 未达到声明口径

- `CreateInvestigationRequest` 要求调用方提供 services、start_time、end_time；`parse_incident()` 没有从 `raw_query` 提取它们。PRD 的“自然语言解析”应标为部分实现。
- `GraphNodes._model_context()` 只传 `incident.services[0]`，而 `generate_report()` 直接把全部 `incident.services` 写入 affected_services。第二个及以后服务没有被实际调查却可能被报告为受影响。
- 默认模型的 `_plan()` 固定查询 `connection acquisition`、`db.pool.utilization`、`POST /payments` 和 `connection pool timeout`。checkout DNS 与 inventory cache regression 的计划参数不符合场景。
- `_hypotheses()` 只产出 supporting evidence；运行链没有形成 contradicting evidence 和 rejected hypotheses。领域模型存在并不等于功能闭环存在。
- 原始 Evidence 没有 Repository/resolver；Graph 只保留 EvidenceRef。引用可以给出来源定位，但 API 无法用 evidence ID 取回采集时的原始对象。

## 异常与降级路径

| 场景 | 当前行为 | 判断 |
|---|---|---|
| 单工具失败 | 记录 `InvestigationError`，继续其他证据 | 合理，已有集成测试 |
| LLM/模型结构化输出不合法 | 有限重试，随后明确降级 | 合理，已有测试 |
| 工具 retryable failure | Registry 能重试，但 Graph 上下文只允许一次 attempt | 实际行为与文档不一致 |
| 超预算/超时 | 路由到报告，通常为 inconclusive | 合理 |
| HITL 拒绝/继续研究 | `Command` 路由 END 或 refine | 合理 |
| 进程重启但只有 checkpoint | 可从稳定 ID 重建最小调查记录；历史 SSE 事件丢失 | 本地阶段可接受，应明确限定 |
| API 重复 resume | service lock 与状态校验阻止非法并发 | 合理 |
| 多服务输入 | 仅调查首个服务，报告列出全部服务 | 不正确，需在 P1 修复 |

## 高价值功能缺口

### IC-P1-03：自然语言解析未实现

- 证据：`api/schemas.py:CreateInvestigationRequest` 强制 services/time window；`graph/nodes.py:parse_incident()` 只验证。
- 用户影响：用户必须先完成本应由 Copilot 做的定位工作，PRD 与演示叙事不一致。
- 验收：只提交 raw query 与可选默认时区即可得到经校验的 services、time window、symptoms；不确定字段必须显式请求补充而非猜测。

### IC-P1-04：反证与淘汰链仅有 schema

- 证据：`domain/hypothesis.py`、`domain/report.py:rejected_hypotheses` 有模型；`FakeModelProvider._hypotheses()` 与 `GraphNodes.generate_report()` 不产生完整链路。
- 用户影响：报告看似“验证假设”，实际默认路径只有支持证据，难以证明系统会主动反驳自身。
- 验收：至少一个 fixture 产生竞争假设、反证和明确淘汰理由；API Demo 可见该过程，测试覆盖 fabricated evidence ID 过滤。

### IC-P1-05：多服务输入会过报

- 证据：模型上下文只选首个服务；报告 `affected_services=state["incident"].services`。
- 用户影响：结论可能把未调查服务标为受影响，破坏报告可信度。
- 验收：要么入口限制为恰好一个服务，要么按服务分派计划并只从有证据的服务生成 affected_services。

## 功能审查结论

以“离线、单主服务、确定性支付故障教学 Demo”为边界，功能完整度高；以 PRD 的通用事故 Copilot 边界，当前属于可用骨架加一条深做场景。下一步不应增加更多 UI 或 Provider，而应先让已有三类 fixture 的计划、正反证和引用完整性真正成立。
