"""Guards on the migration history."""

from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_no_migration_touches_langgraph_checkpoint_tables():
    """LangGraph owns checkpoint_* (created by AsyncPostgresSaver.setup()). A migration that
    drops them destroys every paused workflow."""
    offenders = [p.name for p in VERSIONS.glob("*.py") if "checkpoint" in p.read_text()]
    assert offenders == []
