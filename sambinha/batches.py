"""Stable batch membership and size-only packing in source traversal order."""
import hashlib
import json
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .inventory import File


@dataclass(frozen=True)
class Batch:
    key: str
    files: tuple[File, ...]
    archive_size: int | None = None
    archive_md5: str | None = None
    individual: bool | None = None
    batch_id: str | None = None
    passthrough: bool = False

    def control(self):
        control = {"schema": 1, "archive": self.key,
                   "files": [f.metadata() for f in self.files]}
        if self.archive_size is not None:
            control.update(archive_size=self.archive_size, archive_md5=self.archive_md5)
        if self.individual is not None:
            control['individual'] = self.individual
        if self.batch_id is not None:
            control['batch_id'] = self.batch_id
        if self.passthrough:
            control['passthrough'] = True
        return control


def individual(control):
    """Preserve dedicated roles, including singleton controls from older releases."""
    return len(control['files']) == 1 or control.get('individual', False)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def _make(members, extension, dedicated=False, batch_id=None):
    members = tuple(members)
    batch_id = batch_id or _digest([f.metadata() for f in members])
    digest = _digest({'files': [f.metadata() for f in members],
                      'individual': dedicated, 'batch_id': batch_id})
    parent = posixpath.commonpath([str(PurePosixPath(f.path).parent) for f in members])
    passthrough = dedicated and len(members) == 1 and PurePosixPath(members[0].path).suffix.lower() in {
        '.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.tbz', '.tbz2', '.xz', '.txz',
        '.zst', '.tzst', '.lz', '.lzma', '.lz4', '.br', '.cab', '.arj', '.ace', '.z',
    }
    if dedicated or len(members) == 1:
        name = (PurePosixPath(members[0].path).name if passthrough else
                PurePosixPath(members[0].path).with_suffix('.' + extension).name)
        key = str(PurePosixPath(parent) / '.versions' / digest / name)
    else:
        key = str(PurePosixPath(parent) / f'batch-{digest}.{extension}')
    return Batch(key, members, individual=dedicated, batch_id=batch_id, passthrough=passthrough)


def pack(files, max_bytes, extension='7z', individual_percent=100):
    """Keep a normal batch open while diverting individual files; no count cap."""
    chunks: list[tuple[list[File], bool]] = []
    current: list[File] | None = None
    size = 0
    for item in files:
        if item.size * 100 >= max_bytes * individual_percent:
            chunks.append(([item], True))
            continue
        if current is None or size + item.size > max_bytes:
            current = []
            chunks.append((current, False))
            size = 0
        current.append(item)
        size += item.size
    return [_make(members, extension, dedicated) for members, dedicated in chunks]


def plan(files: list[File], max_bytes: int, max_files: int | None = None, extension: str = '7z',
         previous: dict[str, dict[str, Any]] | None = None, category: str | None = None, *,
         individual_percent: int = 100, rebalance: bool = False) -> list[Batch]:
    """Keep existing groups, even oversized; pack only new or manually moved files.

    Legacy Python max_files/category arguments are ignored. Source inventories
    supply DFS order; there is no reordering by current dates of known members.
    """
    remaining = {item.path: item for item in files}
    result: list[Batch] = []
    moved: list[File] = []
    for key, control in (previous or {}).items():
        members = tuple(remaining.pop(f['path']) for f in control['files'] if f['path'] in remaining)
        if not members:
            continue
        dedicated = individual(control)
        if rebalance and not dedicated and len(members) > 1 and sum(f.size for f in members) > max_bytes:
            moved.extend(members)
            continue
        if (rebalance or key.endswith('.' + extension) or control.get('passthrough')) and [
                f.metadata() for f in members] == control['files']:
            result.append(Batch(key, members, control.get('archive_size'), control.get('archive_md5'),
                                control.get('individual'), control.get('batch_id'), control.get('passthrough', False)))
        else:
            result.append(_make(members, extension, dedicated,
                                control.get('batch_id') or _digest(key)))
    result.extend(pack([*moved, *remaining.values()], max_bytes, extension, individual_percent))
    return result
