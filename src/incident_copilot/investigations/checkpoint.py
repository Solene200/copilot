"""Explicit LangGraph checkpointer composition for local and production targets."""

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
    """Open one saver for the entire application lifespan and initialize its schema."""
    if settings.checkpoint_backend is CheckpointBackend.MEMORY:
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
        await saver.setup()
        yield cast(BaseCheckpointSaver[str], saver)
