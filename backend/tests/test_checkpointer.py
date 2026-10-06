from app.agents.checkpointer import psycopg_dsn


def test_dsn_conversion_keeps_ssl_requirement():
    url = "postgresql+asyncpg://u:p@db.example:5432/postgres?ssl=require"
    assert psycopg_dsn(url) == "postgresql://u:p@db.example:5432/postgres?sslmode=require"
    assert psycopg_dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
