"""Three-tier compression plan: text, other and already-compressed members of one batch.

Each member is classified by file extension (see ``extensions``) and the batch is split into ordered
groups, each compressed at its own level by one append to the same archive. Order matters: appending
copies the existing archive stream, so the smallest, cheapest group goes first (text, then other, then
already-compressed data stored as is).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from .extensions import extension_of, lookup

TEXT = "text"
OTHER = "other"
COMPRESSED = "compressed"
ORDER = (TEXT, OTHER, COMPRESSED)


class Group(NamedTuple):
    kind: str
    level: int
    files: tuple[Any, ...]

    @property
    def bytes(self) -> int:
        return sum(item.size for item in self.files)


def _kind(path: str, text: frozenset[str], compressed: frozenset[str]) -> str:
    extension = extension_of(path)
    if extension in text:
        return TEXT
    return COMPRESSED if extension in compressed else OTHER


def classify(path: str, job: Any) -> str:
    """Tier of a batch-relative path: ``text``, ``compressed`` or ``other`` (listed in neither)."""
    text, compressed = lookup(tuple(job.compression_text_extensions), tuple(job.compression_compressed_extensions))
    return _kind(path, text, compressed)


def plan_groups(job: Any, files: Sequence[Any]) -> list[Group]:
    """Non-empty groups in command order (text, other, compressed); batch order kept inside each."""
    text, compressed = lookup(tuple(job.compression_text_extensions), tuple(job.compression_compressed_extensions))
    buckets: dict[str, list[Any]] = {kind: [] for kind in ORDER}
    for item in files:
        buckets[_kind(item.path, text, compressed)].append(item)
    levels = {TEXT: job.compression_text_level, OTHER: job.compression_other_level,
              COMPRESSED: job.compression_compressed_level}
    return [Group(kind, levels[kind], tuple(buckets[kind])) for kind in ORDER if buckets[kind]]


def summary(groups: Sequence[Group]) -> dict[str, dict[str, int]]:
    """Per-tier file count, byte total and level, for events."""
    return {g.kind: {"files": len(g.files), "bytes": g.bytes, "level": g.level} for g in groups}


__all__ = ["COMPRESSED", "ORDER", "OTHER", "TEXT", "Group", "classify", "plan_groups", "summary"]
