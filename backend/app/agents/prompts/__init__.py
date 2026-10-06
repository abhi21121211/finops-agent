"""Versioned prompt files (spec §12 rule 6). Load by name, e.g. load_prompt("extract_v1")."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")
