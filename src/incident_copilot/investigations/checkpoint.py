"""Explicit LangGraph checkpointer composition for local and production targets.

中文教学说明:Checkpointer 保存的是 LangGraph 以 ``thread_id`` 区分的执行快照,使
``interrupt`` 后的工作流可以恢复;它不等同于 Investigation/Event 业务仓储。默认内存
实现便于离线测试,PostgreSQL 实现用于跨应用进程恢复演示。
"""

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.core.config import CheckpointBackend, Settings
from incident_copilot.core.exceptions import ConfigurationError


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """Open one saver for the entire application lifespan and initialize its schema.

    中文:上下文管理器确保连接资源覆盖所有 Graph 调用。调用方编译 Graph 后,每次执行
    仍必须在 RunnableConfig 中传入稳定 ``thread_id``,否则无法关联历史 checkpoint。
    """
    if settings.checkpoint_backend is CheckpointBackend.MEMORY:
        # 中文:零依赖路径适合单进程测试;进程退出后快照不会保留。
        yield InMemorySaver()
        return
    if settings.postgres_dsn is None:
        raise ConfigurationError("PostgreSQL checkpoint backend requires postgres_dsn")
    try:
        module = importlib.import_module("langgraph.checkpoint.postgres.aio")
    except ImportError as exc:
        raise ConfigurationError(
            "PostgreSQL checkpoint backend requires the 'postgres' project extra"
        ) from exc
    saver_type = cast(Any, module).AsyncPostgresSaver
    manager = saver_type.from_conn_string(settings.postgres_dsn.get_secret_value())
    async with manager as saver:
        # 中文:官方 saver 自己维护 checkpoint 表;setup 不创建项目的业务领域表。
        await saver.setup()
        yield cast(BaseCheckpointSaver[str], saver)
