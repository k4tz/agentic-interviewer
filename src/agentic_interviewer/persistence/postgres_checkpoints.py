from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PostgresCheckpointSettings:
    connection_uri: str
    initialize_schema: bool = False
    require_strict_msgpack: bool = True


@contextmanager
def compile_with_postgres_checkpoints(
    graph_builder: Any, settings: PostgresCheckpointSettings
) -> Iterator[Any]:
    """Compile a graph with a production saver while keeping its connection lifecycle explicit.

    The optional dependency and database connection are loaded only when this context manager
    is entered. Schema creation is opt-in so application startup never mutates production data.
    """
    strict_msgpack = os.environ.get("LANGGRAPH_STRICT_MSGPACK", "").lower()
    if settings.require_strict_msgpack and strict_msgpack not in {
        "1",
        "true",
    }:
        raise RuntimeError(
            "Set LANGGRAPH_STRICT_MSGPACK=true before opening production checkpoints"
        )
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Postgres checkpoints require the 'langgraph-checkpoint-postgres' package"
        ) from exc

    with PostgresSaver.from_conn_string(settings.connection_uri) as checkpointer:
        if settings.initialize_schema:
            checkpointer.setup()
        yield graph_builder.compile(checkpointer=checkpointer)
