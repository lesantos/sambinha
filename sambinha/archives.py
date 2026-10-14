"""Snapshot, compress and validate a batch before any upload."""
from pathlib import Path
import shutil

from .errors import BackupError
from .inventory import verify
from .process import show_progress
from .profiles import settings


def compress(ctx, batch, work: Path):
    snapshot = work / "snapshot"
    snapshot.mkdir()
    preparing = ctx.progress.scope(0, 0.2) if ctx.progress else None
    for index, item in enumerate(batch.files):
        if preparing:
            preparing.update("Preparando lote", (ctx.job.source / item.path).parent, index, len(batch.files))
        source, target = ctx.job.source / item.path, snapshot / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    verify(snapshot, list(batch.files), mode=ctx.config.verification_mode,
           progress=ctx.progress.scope(0.2, 0.25) if ctx.progress else None)
    verify(ctx.job.source, list(batch.files), mode=ctx.config.verification_mode,
           progress=ctx.progress.scope(0.25, 0.3) if ctx.progress else None)
    archive = work / ("archive." + ctx.job.archive_format)
    listing = work / "files.txt"
    listing.write_text("".join(f"./{f.path}\n" for f in batch.files), encoding="utf-8")
    # @listfile and -spd prevent option/wildcard interpretation of filenames.
    kind, level, threads = settings(ctx.job, batch.files)
    directory = (ctx.job.source / batch.files[0].path).parent
    show_progress(ctx.runner, ctx.progress.scope(0.3, 0.6) if ctx.progress else None)
    ctx.runner.run([ctx.job.compressor, "a", "-t" + ctx.job.archive_format,
                    "-scsUTF-8", "-spd", "-spf", f"-mx={level}", f"-mmt={threads}",
                    str(archive), "@" + str(listing)], cwd=snapshot, progress_directory=directory)
    show_progress(ctx.runner, ctx.progress.scope(0.6, 0.7) if ctx.progress else None)
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
    return archive
