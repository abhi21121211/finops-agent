"""Postgres checkpointer: every graph step is saved, so a run paused at human_review (or
interrupted by a crash) resumes exactly where it stopped, even after a restart."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings


def psycopg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def postgres_checkpointer(max_size: int = 5):
    pool = AsyncConnectionPool(
        psycopg_dsn(get_settings().database_url),
        max_size=max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()  # idempotent; creates the checkpoint tables
        yield saver
    finally:
        await pool.close()
