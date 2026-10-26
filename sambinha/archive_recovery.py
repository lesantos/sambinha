"""Validate and unpack archive members into private staging directories."""
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil

from .disks import check_disk
from .errors import BackupError
from .inventory import fingerprint, metadata as file_metadata, safe_relative, scan


def check_content(root, metadata, *, mode=None, check_mtime=False):
    mode = mode or ("hash" if metadata["sha256"] and metadata["md5"] else "simple")
    if mode == "hash" and not (metadata["sha256"] and metadata["md5"]):
        raise BackupError("Índice sem hashes; execute um backup com verification_mode=hash antes de verificar por hash")
    actual = fingerprint(root, metadata["path"]) if mode == "hash" else file_metadata(root, metadata["path"])
    keys = ("size", "sha256", "md5") if mode == "hash" else (("size", "mtime_ns") if check_mtime else ("size",))
    if any(getattr(actual, key) != metadata[key] for key in keys):
        raise BackupError(f"Integridade divergente: {metadata['path']}")


def archive_members(storage, runner, archive):
    """Read safe file entries without extracting archive contents."""
    # Keep 7-Zip and Python on UTC, independent of historical DST rules.
    listing = runner.run([storage.job.compressor, "l", "-slt", "-ba", str(archive)], env={"TZ": "UTC"})
    members = {}
    for record in listing.strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in record.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = fields["Path"].removeprefix("./")
        safe_relative(name)
        if ("\\" in name or any(k in fields for k in ("Symbolic Link", "Hard Link"))
                or re.search(r"(?:^|\s)[lbcp]", fields.get("Attributes", ""))):
            raise BackupError("Lote contém link ou arquivo especial")
        if fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D"):
            continue
        if name in members:
            raise BackupError("Arquivo duplicado no lote")
        members[name] = fields
    return members


def archive_date_matches(fields, metadata, archive_format):
    """Compare UTC timestamps at the archive format's precision."""
    modified = fields.get("Modified", "")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?", modified)
    if not match:
        raise BackupError(f"Data de modificação ausente ou inválida no lote: {metadata['path']}")
    seconds, nanoseconds = divmod(metadata["mtime_ns"], 10 ** 9)
    expected = datetime.fromtimestamp(seconds, timezone.utc)
    actual = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    fraction = int((match[2] or '').ljust(9, '0'))
    characteristics = fields.get("Characteristics", "").replace(':', ' ').upper().split()
    if archive_format == "zip" and "NTFS" not in characteristics:
        # Older ZIP entries can store whole seconds (UT) or DOS two-second timestamps.
        resolution = 1 if "UT" in characteristics else 2
        return expected.replace(second=expected.second // resolution * resolution) == actual and fraction == 0
    # 7z and ZIP/NTFS timestamps use 100 ns units; listing can omit trailing zeros.
    return expected == actual and nanoseconds // 100 == fraction // 100


def check_archive(storage, runner, key, control, *, archive=None):
    """Verify paths, sizes, dates and 7-Zip integrity without disk extraction or hashes."""
    # storage.path returns an rclone-style string; local in-place checks need a Path.
    archive = Path(storage.path(key) if archive is None else archive)
    if control.get('passthrough'):
        actual = fingerprint(archive.parent, archive.name)
        item = control['files'][0]
        if (len(control['files']) != 1 or actual.size != item['size']
                or item.get('md5') and actual.md5 != item['md5']
                or 'archive_md5' in control and
                (actual.size != control['archive_size'] or actual.md5 != control['archive_md5'])):
            raise BackupError(f"Arquivo compactado individual divergente: {key}")
        return
    members = archive_members(storage, runner, archive)
    if {name: int(fields["Size"]) for name, fields in members.items()} != {
            item["path"]: item["size"] for item in control["files"]}:
        raise BackupError(f"Lote incompleto ou caminhos/tamanhos diferentes do índice: {key}")
    for item in control["files"]:
        if not archive_date_matches(members[item["path"]], item, key.rsplit('.', 1)[-1]):
            raise BackupError(f"Data de modificação divergente no lote: {item['path']}")
    runner.run([storage.job.compressor, "t", str(archive)])


def unpack(storage, runner, key, control, work):
    archive = work / "archive"
    storage.get(key, archive)
    if "archive_md5" in control:
        actual = fingerprint(work, "archive")
        if actual.size != control["archive_size"] or actual.md5 != control["archive_md5"]:
            raise BackupError(f"Checksum do lote divergente: {key}")
    if control.get('passthrough'):
        if len(control['files']) != 1:
            raise BackupError("Lote individual inválido")
        metadata = control['files'][0]
        extracted = work / 'files'
        extracted.mkdir(mode=0o700)
        target = extracted / metadata['path']
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
        check_content(extracted, metadata, mode='hash' if metadata.get('sha256') else 'simple')
        return extracted
    members = archive_members(storage, runner, archive)
    if {name: int(fields["Size"]) for name, fields in members.items()} != {
            f["path"]: f["size"] for f in control["files"]}:
        raise BackupError("Lote incompleto ou caminhos diferentes do índice")
    runner.run([storage.job.compressor, "t", str(archive)])
    extracted = work / "files"
    extracted.mkdir(mode=0o700)
    check_disk(extracted, "temporários de recuperação")
    runner.run([storage.job.compressor, "x", "-y", "-spd", "-o" + str(extracted), str(archive)])
    actual = scan(extracted, mode="simple")
    if {f.path for f in actual} != set(members):
        raise BackupError("Conteúdo extraído diferente do índice")
    for metadata in control["files"]:
        check_content(extracted, metadata)
    return extracted
