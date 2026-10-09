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
    archive_size: int | None = None
    archive_md5: str | None = None

    def control(self):
        control = {"schema": 1, "archive": self.key,
                   "files": [f.metadata() for f in self.files]}
        if self.archive_size is not None:
            control.update(archive_size=self.archive_size, archive_md5=self.archive_md5)
        return control


def plan(files: list[File], max_bytes: int, max_files: int, extension="7z",
         previous: dict[str, dict] | None = None, category=None) -> list[Batch]:
    # Preserve every intact, compatible archive, including its original member
    # order. New files must not shift the boundaries of already stored lots.
    remaining = {item.path: item for item in files}
    result = []
    for key, control in sorted((previous or {}).items()):
        members = tuple(File(**metadata) for metadata in control["files"])
        size = sum(item.size for item in members)
        if (members and key.endswith("." + extension) and len(members) <= max_files
                and (size <= max_bytes or len(members) == 1)
                and all(remaining.get(item.path) == item for item in members)):
            result.append(Batch(key, members, control.get("archive_size"), control.get("archive_md5")))
            for item in members:
                remaining.pop(item.path)
    # Files from each directory are contiguous. Small directory groups can
    # share a batch with other directories; oversized groups may span batches.
    groups = {}
    for item in remaining.values():
        groups.setdefault(str(PurePosixPath(item.path).parent), []).append(item)
    ordered = [item for parent in sorted(groups)
               for item in sorted(groups[parent], key=lambda f: (f.mtime_ns, f.path))]
    chunks, current, size = [], [], 0
    for item in ordered:
        if current and (size + item.size > max_bytes or len(current) >= max_files
                        or category is not None and category(item.path) != category(current[-1].path)):
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
        if (len(chunk) == 1 and len(previous) > 2 and chunk[0].size + previous[-1].size <= max_bytes
                and (category is None or category(chunk[0].path) == category(previous[-1].path))):
            chunk.insert(0, previous.pop())
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
