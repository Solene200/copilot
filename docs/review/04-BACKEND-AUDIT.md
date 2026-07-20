# Python 后端与架构审查

## 结论

后端分层符合一个优秀作品集项目应有的复杂度：domain 不依赖 FastAPI/LangGraph/SQLAlchemy，Graph 只依赖端口，Provider 负责外部读取，API 负责协议转换和装配，调查记录与 checkpoint 被明确区分。Pydantic v2、timezone-aware datetime、结构化异常、请求体/结果/时间/预算上限都落实到了代码。

当前不是生产后端：调查 Repository 固定为内存，无鉴权/租户/限流，Evidence 无持久化 resolver，Postgres checkpoint 与 pgvector 本次未真实连接。README 对大部分限制已有披露，问题在少数架构文档仍把规划写成现状。

## 分层与依赖方向

| 层 | 实际职责 | 评价 |
|---|---|---|
| `domain/` | Incident、Evidence/Citation、Hypothesis、Report、Review 值对象与不变量 | 边界干净，类型价值高 |
| `graph/` | State、节点、路由、builder、ModelProvider | 编排集中；`nodes.py` 较大但主线可追踪 |
| `tools/interfaces.py` | 七类只读端口与 QueryContext/ToolResult | 合理的替换边界 |
| `tools/providers/` | fixture、RAG、Prometheus 适配 | 默认离线可替换为真实指标源 |
| `application/` | 调查生命周期、后台任务、事件投影、恢复 | 服务边界清楚 |
| `api/` | 请求/响应、SSE、health | 没有调查业务逻辑泄漏 |
| `storage/` / checkpoint | 调查记录与 Graph checkpoint | 概念区分正确，生产实现不完整 |

没有发现 domain 反向依赖 FastAPI、LangGraph 或供应商 SDK。`main.py:create_app()` 是主要 composition root；fixture/prometheus、in-memory/Postgres checkpointer 都从设置选择。

## Pydantic、时间与外部输入

- API 请求、工具参数、模型结构化输出、fixture、RAG metadata 都经 Pydantic v2。
- `AwareDatetime` 统一拒绝 naive datetime，持久化边界使用 UTC。
- URI scheme、service name、结果条数、时间跨度、查询长度、HTTP 响应大小与 points 数都有上限。
- 模型输出被视为不可信；`GraphNodes._call_model()` 校验失败有限重试并保留结构化错误。
- Tool Registry 再次校验服务 allowlist、时间窗、deadline 与结果数量，避免只依赖 Provider 自觉。

需要注意：`Citation.content_hash` 只校验“64 位十六进制”以及 Evidence/Citation 两字段相等，没有对 `Evidence.content` 做 canonical SHA-256。类型看起来强，但核心语义不变量并未实现，这是 P0 的根因。

## 异常处理与可观测性

没有发现空 `except` 或大面积吞异常。Provider 的 `ToolFailure` 保留 category/retryable/message；跨层失败进入 `InvestigationError`，报告 limitation 会指出降级。Prometheus 使用 `asyncio.to_thread()` 包装阻塞 urllib，并限制 URL、响应大小、样本数和 metric allowlist。

日志/事件具有 request、investigation、thread、run 关联 ID。SSE 投影覆盖 node、tool、evidence、hypothesis、budget、review 与 terminal event，实际链路可观测性强。

P2 问题：客户端提供的 `X-Request-ID` 没有明确长度/字符约束就进入上下文和日志。虽然默认 localhost 风险低，公网化前应生成服务端规范 ID，原值只作受限 metadata，防止日志注入和高基数字段。

## API 生命周期与并发

`InvestigationService` 为每个调查维护后台 task 与 lock，阻止同一调查并发 resume。In-memory repository 用锁保护记录和事件追加，并校验事件 sequence。创建响应不会等待完整 Graph，SSE 可读取真实中间更新。

潜在边界：

- 进程退出会丢失内存记录和事件；即便 Postgres checkpoint 存在，恢复出的也是最小记录，历史 SSE 不可还原。
- API 没有 idempotency key；客户端在 create 响应丢失后重试会生成两个调查。
- 多 worker 下每进程内存 lock/repository 不共享，因此当前 server 只应单进程演示。
- 没有任务队列和 backpressure；并发大量调查会在 API 进程内创建大量 asyncio tasks。

这些是明确的生产化输入，不是当前本地 Phase 必须全部解决的缺陷。

## 安全边界

做得好的部分：只读工具、无自动执行 remediation、HITL 审核高风险动作、API 默认 localhost、无秘密入库、fixture 脱敏、Prometheus metric allowlist 与响应上限。

缺失部分：鉴权、授权、租户隔离、API 级限流、审计日志持久化、CORS/代理信任策略、请求 ID 规范化。因项目明确定位本地演示，这些归为 P3；若要公网部署则自动升级为 P0/P1 前置条件。

## 依赖与配置

`uv.lock` 可验证，Python 3.11–3.13 范围明确。依赖规模与能力基本匹配，没有为简单功能引入大型 agent 框架。`Settings.model_api_key` 当前保留但没有任何真实 ModelProvider 消费，属于 P3 清理项：要么在接入真实模型时使用，要么删除，避免暗示已支持。

## 后端问题清单

### IC-P2-06：checkpoint 与应用记录的恢复能力不对称

- 影响：Graph 可恢复但调查元数据/完整事件不可恢复，用户会看到状态而非完整审计轨迹。
- 复现：使用持久 checkpoint、丢弃 in-memory repository 后走 `_recover_from_checkpoint()`，只重建最小记录。
- 修复：若进入持久化阶段，为 Investigation/Run/Event 建独立 repository 与迁移；不要把 checkpoint 当业务数据库。
- 验收：重启后 status、所有 SSE event、review history 与 run history 一致恢复。

### IC-P2-07：客户端请求 ID 未规范化

- 影响：日志注入、高基数或异常长上下文；本地环境影响低。
- 修复：服务端生成 canonical ID；外部 ID 限长、限字符后放单独字段。
- 验收：恶意换行、超长 ID 被拒绝/截断且日志仍为单条结构化记录。

### IC-P3-01：调查存储、鉴权与多 worker 尚未生产化

- 影响：不可公网部署或横向扩展。
- 修复：只有在目标变为共享服务时再实施持久 Repository、认证授权、分布式锁/队列和限流。
- 验收：重启、多 worker、跨用户访问和并发压测具有明确 SLO 与隔离测试。

## 架构建议

保留现有 domain/graph/tools/application/api 分层，不要重写成通用 agent platform。下一轮最有价值的架构变化只有两项：建立可校验的 Evidence Store/Resolver；修正工具 attempt 的全局预算语义。其余生产化能力应由明确部署目标触发。
