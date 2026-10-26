"""Compress and validate a batch directly from its source before upload."""
import json
import os
from pathlib import Path

from .disks import check_disk
from .errors import BackupError
from .inventory import verify
from .process import show_progress
from .profiles import settings


def temporary_bytes(batch):
    """Estimate one incompressible archive plus staging overhead."""
    metadata = len(json.dumps(batch.control(), ensure_ascii=False).encode("utf-8"))
    overhead = max(1024 ** 2, 16384 * len(batch.files) + 2 * metadata)
    if batch.passthrough:
        return overhead
    return sum(item.size for item in batch.files) + overhead


# Linux accepts 2 MiB of arguments plus environment and macOS 1 MiB; stay far
# below both so naming members on the command line never fails on length.
ARGUMENT_BUDGET = 256 * 1024


def member_arguments(files, listing: Path):
    """Name batch members on the command line whenever they fit.

    7-Zip trims trailing spaces and tabs from listfile lines, so such files
    are "not found" and the job fails forever. Arguments are passed verbatim;
    the listfile is only a fallback for huge batches, and it refuses names
    it cannot express instead of producing a misleading 7-Zip error.
    """
    paths = [item.path for item in files]
    if sum(len(os.fsencode(path)) + 1 for path in paths) <= ARGUMENT_BUDGET:
        return ["--", *paths]
    for path in paths:
        if path[-1] in " \t":
            raise BackupError("Nome de arquivo termina com espaço ou tabulação e o lote é grande demais "
                              f"para a linha de comando; renomeie ou reduza max_megabytes: {path!r}")
    listing.write_text("".join(f"./{path}\n" for path in paths), encoding="utf-8")
    return ["@" + str(listing)]


def compress(ctx, batch, work: Path):
    if batch.passthrough:
        item = batch.files[0]
        # The file is uploaded straight from the source, so a single check
        # that it still matches the inventory is all compression can offer.
        verify(ctx.source, [item], mode=ctx.job.verification_mode,
               progress=ctx.progress.scope(0, 1) if ctx.progress else None,
               verify_source_changes=ctx.job.verify_source_changes)
        return ctx.source / item.path
    check_disk(work, "temporários", required_bytes=temporary_bytes(batch))
    # Validate both sides of the read: the inventory must still match before
    # 7-Zip opens source paths and after it has finished reading them.
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0, 0.1) if ctx.progress else None,
           verify_source_changes=ctx.job.verify_source_changes)
    archive = work / ("archive." + ctx.job.archive_format)
    members = member_arguments(batch.files, work / "files.txt")
    # -spd prevents wildcard interpretation of filenames; "--" ends switch
    # parsing so names starting with "-" or "@" are taken literally.
    _kind, level, threads = settings(ctx.job, batch.files)
    directory = (ctx.job.source / batch.files[0].path).parent
    show_progress(ctx.runner, ctx.progress.scope(0.1, 0.7) if ctx.progress else None)
    ctx.runner.run([ctx.job.compressor, "a", "-t" + ctx.job.archive_format,
                    "-scsUTF-8", "-spd", "-spf", f"-mx={level}", f"-mmt={threads}",
                    str(archive), *members], cwd=ctx.source, progress_directory=directory)
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0.7, 0.8) if ctx.progress else None,
           verify_source_changes=ctx.job.verify_source_changes)
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
