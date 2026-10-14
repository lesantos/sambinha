"""Path overlap rules shared by configuration and restoration."""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Job


def overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def destinations_overlap(first: Job, second: Job) -> bool:
    if first.type != second.type:
        return False
    if first.type == "local":
        return overlap(Path(first.destination).resolve(), Path(second.destination).resolve())
    def prefix(value):
        remote, path = value.split(":", 1)
        parts = tuple(part for part in path.split("/") if part)
        return remote, parts
    first_remote, first_parts = prefix(first.destination)
    second_remote, second_parts = prefix(second.destination)
    return (first_remote == second_remote and
            (first_parts[:len(second_parts)] == second_parts or
             second_parts[:len(first_parts)] == first_parts))
