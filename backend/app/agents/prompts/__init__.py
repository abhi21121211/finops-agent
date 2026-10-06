"""Versioned prompt files (spec §12 rule 6). Load by name, e.g. load_prompt("extract_v1")."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    """By version name (`extract_v3`) or, for experiments, a path to a .md file."""
    path = Path(name) if name.endswith(".md") else _DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
