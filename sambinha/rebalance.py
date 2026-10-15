"""Manual rebalance of oversized active groups, independent of the source."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import tempfile

from .archive_recovery import unpack
from .batches import individual
from .context import Context
from .disks import check_disks
from .errors import BackupError
from .filtering import Selection
from .index_generations import backup_state
from .inventory import File
from .mounts import check_mounts
from .strategies.batch import batch


def execute(config, job, runner, log, progress, forecast, now, *, on_context=None):
    if job.logic != 'batch':
        raise BackupError('rebalance exige logic=batch')
    runner.require([job.compressor])
    check_mounts(job, runner, check_source=False)
    check_disks(job, check_source=False)
    state = backup_state(config, job, runner, allow_new=False, log=log)
    files = [File(**f) for c in state['active'].values() for f in c['files']]
    effective = replace(config, jobs=(replace(job, verification_mode='simple'),))
    job = effective.jobs[0]
    ctx = Context(effective, job, runner, log, files, now, state=state, rebalancing=True,
                  progress=progress, forecast=forecast)
    if on_context:
        on_context(ctx)
    ctx.selection = Selection(job.exclude_files, effective, runner)
    affected = {key: control for key, control in state['active'].items()
                if not individual(control) and len(control['files']) > 1
                and sum(f['size'] for f in control['files']) > job.max_bytes}
    ctx.event('rebalance_summary', affected_batches=len(affected), preserved_batches=len(state['active']) - len(affected),
              dry_run=job.dry_run)
    if not affected:
        if state.get("index_pending"):
            batch(ctx)
        return ctx
    with tempfile.TemporaryDirectory(prefix='sambinha-rebalance-') as temp:
        work = Path(temp)
        ctx.source = work / 'source'
        ctx.source.mkdir()
        ctx.storage.temporary_directory = work
        if not job.dry_run:
            for key, control in affected.items():
                with tempfile.TemporaryDirectory(dir=work) as lot:
                    extracted = unpack(ctx.storage, runner, key, control, Path(lot))
                    for f in control['files']:
                        target = ctx.source / f['path']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(extracted / f['path'], target)
                        target.chmod(f['mode'] & 0o777)
                        os.utime(target, ns=(f['mtime_ns'], f['mtime_ns']))
        batch(ctx)
    return ctx
