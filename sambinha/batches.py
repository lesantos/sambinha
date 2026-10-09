"""Deterministic tree packing, independent from compression and storage."""
from dataclasses import dataclass
import hashlib
import json
import posixpath
from pathlib import PurePosixPath

from .inventory import File


@dataclass(frozen=True)
class Batch:
    key: str
    files: tuple[File, ...]

    def control(self):
        return {"schema": 1, "archive": self.key,
                "files": [f.metadata() for f in self.files]}


def plan(files: list[File], max_bytes: int, max_files: int, extension="7z") -> list[Batch]:
    # Files from each directory are contiguous. Small directory groups can
    # share a batch with other directories; oversized groups may span batches.
    groups = {}
    for item in files:
        groups.setdefault(str(PurePosixPath(item.path).parent), []).append(item)
    ordered = [item for parent in sorted(groups)
               for item in sorted(groups[parent], key=lambda f: f.path)]
    chunks, current, size = [], [], 0
    for item in ordered:
        if current and (size + item.size > max_bytes or len(current) >= max_files):
            chunks.append(current)
            current, size = [], 0
        if item.size > max_bytes:
            chunks.append([item])
            continue
        current.append(item)
        size += item.size
    if current:
        chunks.append(current)
    # Repair a trailing singleton by borrowing from an adjacent larger batch
    # when both resulting batches obey the limits and contain at least two.
    for index in range(1, len(chunks)):
        previous, chunk = chunks[index - 1], chunks[index]
        if len(chunk) == 1 and len(previous) > 2 and chunk[0].size + previous[-1].size <= max_bytes:
            chunk.insert(0, previous.pop())
    result = []
    for chunk in chunks:
        digest = hashlib.sha256(json.dumps([f.metadata() for f in chunk],
            sort_keys=True).encode()).hexdigest()[:24]
        parent = posixpath.commonpath([str(PurePosixPath(f.path).parent) for f in chunk])
        if len(chunk) == 1:
            name = PurePosixPath(chunk[0].path).with_suffix("." + extension).name
            # Immutable revisions preserve the requested filename and prevent
            # collisions between foo.txt/foo.csv or edits to a singleton.
            key = str(PurePosixPath(parent) / ".versions" / digest / name)
        else:
            key = str(PurePosixPath(parent) / f"batch-{digest}.{extension}")
        result.append(Batch(key, tuple(chunk)))
    return result
