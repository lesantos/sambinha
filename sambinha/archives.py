"""Compress and validate a batch directly from its source before upload."""
import json
import os
from pathlib import Path

from .disks import check_disk
from .errors import BackupError, SourceChangedError
from .inventory import verify
from .process import show_progress
from .profiles import plan_groups


def temporary_bytes(batch, job=None):
    """Estimate the archive(s) held on disk at once plus staging overhead.

    One group: a single incompressible archive. Several groups (``job`` given): every command after the
    first appends, and 7-Zip copies the existing archive while it writes the new one, so the last append
    holds the previous groups' archive plus a new archive of everything. Bound, assuming no compression
    at all: ``2 * total - last_group_bytes``.
    """
    metadata = len(json.dumps(batch.control(), ensure_ascii=False).encode("utf-8"))
    overhead = max(1024 ** 2, 16384 * len(batch.files) + 2 * metadata)
    if batch.passthrough:
        return overhead
    total = sum(item.size for item in batch.files)
    groups = plan_groups(job, batch.files) if job is not None else []
    if len(groups) > 1:
        total += total - groups[-1].bytes
    return total + overhead


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
               verify_source_changes=ctx.job.verify_source_changes, signatures=ctx.signatures)
        return ctx.source / item.path
    check_disk(work, "temporários", required_bytes=temporary_bytes(batch))
    # Validate both sides of the read: the inventory must still match before
    # 7-Zip opens source paths and after it has finished reading them.
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0, 0.1) if ctx.progress else None,
           verify_source_changes=ctx.job.verify_source_changes, signatures=ctx.signatures)
    archive = work / ("archive." + ctx.job.archive_format)
    # -spd prevents wildcard interpretation of filenames; "--" ends switch
    # parsing so names starting with "-" or "@" are taken literally.
    directory = (ctx.job.source / batch.files[0].path).parent
    # One command per non-empty tier, all on the same archive: the first creates it, the others append
    # without recompressing what is there. The 0.1-0.7 progress scope is split by group bytes.
    groups = plan_groups(ctx.job, batch.files)
    weights = [group.bytes for group in groups]
    if not any(weights):
        weights = [len(group.files) for group in groups]
    done = 0
    for index, group in enumerate(groups):
        members = member_arguments(group.files, work / f"files-{index}.txt")
        start = 0.1 + 0.6 * done / sum(weights)
        done += weights[index]
        show_progress(ctx.runner, ctx.progress.scope(start, 0.1 + 0.6 * done / sum(weights)) if ctx.progress else None)
        ctx.runner.run([ctx.job.compressor, "a", "-t" + ctx.job.archive_format,
                        "-scsUTF-8", "-spd", "-spf", f"-mx={group.level}", f"-mmt={ctx.job.compression_threads}",
                        str(archive), *members], cwd=ctx.source, progress_directory=directory)
    verify(ctx.source, list(batch.files), mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0.7, 0.8) if ctx.progress else None,
           verify_source_changes=ctx.job.verify_source_changes, signatures=ctx.signatures)
    show_progress(ctx.runner, ctx.progress.scope(0.8, 0.9) if ctx.progress else None)
    ctx.runner.run([ctx.job.compressor, "t", str(archive)])
    listing = ctx.runner.run([ctx.job.compressor, "l", "-slt", "-ba", str(archive)])
    members, crcs = {}, {}
    for record in listing.strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in record.splitlines() if " = " in line)
        if "Path" in fields and fields.get("Folder") != "+" and not fields.get("Attributes", "").startswith("D"):
            name = fields["Path"].removeprefix("./")
            if name in members:
                raise BackupError("Arquivo duplicado no lote")
            members[name] = int(fields["Size"])
            # Absent for empty files and for formats without per-member CRCs.
            crcs[name] = int(fields["CRC"], 16) if fields.get("CRC") else None
    if members != {f.path: f.size for f in batch.files}:
        raise BackupError("Lote incompleto ou caminhos incorretos; envio interrompido")
    # The inventory's own read pass must agree with what 7-Zip read for the same member. Only
    # files hashed in this run carry a CRC (hash mode, no cache hit); empty members have none.
    for item in batch.files:
        if item.crc32 is not None and crcs.get(item.path) not in (None, item.crc32):
            raise SourceChangedError(f"CRC32 do 7-Zip diverge do inventário: {item.path}; execute novamente")
    # Upload only after the source still matches the inventoried batch.
    return archive
