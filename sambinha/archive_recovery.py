"""Validate and unpack archive members into private staging directories."""
import re

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


def unpack(storage, runner, key, control, work):
    archive = work / "archive"
    storage.get(key, archive)
    if "archive_md5" in control:
        actual = fingerprint(work, "archive")
        if actual.size != control["archive_size"] or actual.md5 != control["archive_md5"]:
            raise BackupError(f"Checksum do lote divergente: {key}")
    listing = runner.run([storage.job.compressor, "l", "-slt", "-ba", str(archive)])
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
        members[name] = int(fields["Size"])
    if members != {f["path"]: f["size"] for f in control["files"]}:
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
