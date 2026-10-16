"""Compress and validate a batch directly from its source before upload."""
from pathlib import Path
import json

from .errors import BackupError
from .disks import check_disk
from .inventory import verify
from .process import show_progress
from .profiles import settings


def temporary_bytes(batch):
    """Estimate one incompressible archive plus staging overhead."""
    metadata = len(json.dumps(batch.control(), ensure_ascii=False).encode("utf-8"))
    overhead = max(1024 ** 2, 16384 * len(batch.files) + 2 * metadata)
    return sum(item.size for item in batch.files) + overhead


def compress(ctx, batch, work: Path):
    check_disk(work, "temporários", required_bytes=temporary_bytes(batch))
    # Validate both sides of the read: the inventory must still match before
    # 7-Zip opens source paths and after it has finished reading them.
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0, 0.1) if ctx.progress else None)
    archive = work / ("archive." + ctx.job.archive_format)
    listing = work / "files.txt"
    listing.write_text("".join(f"./{f.path}\n" for f in batch.files), encoding="utf-8")
    # @listfile and -spd prevent option/wildcard interpretation of filenames.
    kind, level, threads = settings(ctx.job, batch.files)
    directory = (ctx.job.source / batch.files[0].path).parent
    show_progress(ctx.runner, ctx.progress.scope(0.1, 0.7) if ctx.progress else None)
    ctx.runner.run([ctx.job.compressor, "a", "-t" + ctx.job.archive_format,
                    "-scsUTF-8", "-spd", "-spf", f"-mx={level}", f"-mmt={threads}",
                    str(archive), "@" + str(listing)], cwd=ctx.source, progress_directory=directory)
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0.7, 0.8) if ctx.progress else None)
    show_progress(ctx.runner, ctx.progress.scope(0.8, 0.9) if ctx.progress else None)
    ctx.runner.run([ctx.job.compressor, "t", str(archive)])
    listing = ctx.runner.run([ctx.job.compressor, "l", "-slt", "-ba", str(archive)])
    members = {}
    for record in listing.strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in record.splitlines() if " = " in line)
        if "Path" in fields and fields.get("Folder") != "+" and not fields.get("Attributes", "").startswith("D"):
            name = fields["Path"].removeprefix("./")
            if name in members:
                raise BackupError("Arquivo duplicado no lote")
            members[name] = int(fields["Size"])
    if members != {f.path: f.size for f in batch.files}:
        raise BackupError("Lote incompleto ou caminhos incorretos; envio interrompido")
    # Upload only after the source still matches the inventoried batch.
    return archive
