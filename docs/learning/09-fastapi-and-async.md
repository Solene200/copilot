# 09 FastAPI 与异步任务

## FastAPI 在项目中的边界

API 层只负责:

- Pydantic 请求/响应转换。
- Header 和路径参数。
- HTTP 状态码和错误 envelope。
- SSE StreamingResponse。
- 从 `app.state` 获取 Service。

它不直接调用 Provider, 也不实现 Graph 节点。

## 应用工厂

```python
def create_app(settings=None, investigation_service=None) -> FastAPI:
    ...
```

可选参数用于测试依赖注入。生产路径使用 `get_settings()`, 测试可传独立 Settings 或 Service, 避免修改全局环境。

## Lifespan

```text
应用启动
→ open_checkpointer
→ build graph
→ create InvestigationService
→ 写入 app.state
→ 接收请求
→ 应用关闭
→ service.aclose
→ close checkpointer
```

资源顺序很重要。若先关闭 Checkpointer 再取消后台 Graph, 正在执行的任务可能访问失效连接。

## 为什么 POST 返回 202

创建调查后, Service 使用 `asyncio.create_task()` 启动 `_run_initial()`。因此:

- HTTP 请求只确认“任务已接受”。
- 客户端通过 GET 或 SSE 观察进度。
- 高风险审核可能持续等待人工输入。

202 比 200 更准确, 因为报告尚未完成。

## async/await 的作用

Provider、Graph streaming、Repository 和 SSE 都使用 async API。`await` 会把控制权还给事件循环, 让同一进程处理其他请求。

但 async 不自动保证任何函数都非阻塞。RAG 的 BM25/向量查询是同步 CPU 工作, `RagKnowledgeProvider` 使用 `asyncio.to_thread()` 避免直接阻塞事件循环。

## 进程内 Task 的限制

`InvestigationService._tasks` 保存当前进程创建的任务。它具备:

- 任务命名。
- 完成后清理引用。
- 观察后台异常。
- 应用关闭时 cancel + gather。

它不具备:

- 跨进程 worker。
- lease 或抢占。
- 任务持久化。
- 自动重试整个调查。

后端类比: 这是应用内 background job, 不是消息队列。

## 幂等 POST

相同 Idempotency-Key 和 fingerprint 返回同一任务。相同 key 但 payload 不同返回 409。

Fingerprint 来自规范化请求, 不是直接比较原始 JSON 字节。删除指纹校验会让一个 key 意外代表两个不同调查。

## 错误处理

FastAPI exception handler 把:

- 应用异常映射为稳定业务错误。
- RequestValidationError 映射为 422。
- 未知异常映射为安全 500。

错误消息和 details 在返回前再次脱敏。完整 traceback 只进入服务端日志。

下一步: [Checkpoint、Streaming 与 HITL](10-checkpoint-streaming-hitl.md)。
