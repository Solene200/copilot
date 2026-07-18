"""Checkpointer composition tests that never connect to an external database."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.core.config import CheckpointBackend, RuntimeEnvironment, Settings
from incident_copilot.core.exceptions import ConfigurationError
from incident_copilot.investigations.checkpoint import open_checkpointer


@pytest.mark.asyncio
async def test_memory_checkpointer_is_the_offline_default() -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, _env_file=None)

    async with open_checkpointer(settings) as saver:
        assert isinstance(saver, InMemorySaver)


@pytest.mark.asyncio
async def test_postgres_backend_requires_an_explicit_dsn() -> None:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        checkpoint_backend=CheckpointBackend.POSTGRES,
        _env_file=None,
    )

    with pytest.raises(ConfigurationError, match="requires postgres_dsn"):
        async with open_checkpointer(settings):
            raise AssertionError("context must not open without a DSN")
