"""Immutable batch uploads and transactional recovery-index publication."""
import copy
import json
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from .. import journal, orphans
from ..archives import compress, temporary_bytes
from ..batches import plan
from ..context import Context
from ..disks import check_disk, check_disks, get_temporary_path
from ..errors import BackupError
from ..inventory import fingerprint, path_has_symlink, verify
from ..metadata import cached_control, write_control
from ..names import CONTROL
from ..process import Runner, show_progress
from ..profiles import settings
from ..protection import protect_newer
from ..retention import retirement
from ..state import atomic_json
from ..storage import Storage
from ..tempdirs import prefix


def upload_index(ctx, state):
    from ..index_generations import publish
    publish(ctx, state)


def _check_cancelled(runner):
    # Test doubles may stand in for the runner; only a real token is consulted.
    if isinstance(runner, Runner):
        runner.check_cancelled()


def upload_batch(ctx, item):
    # Each worker owns its runner's last_stderr and its temporary archive.
    worker = copy.copy(ctx)
    worker.runner = copy.copy(ctx.runner)
    worker.progress = ctx.batch_progress.get(item.key) if ctx.progress else None
    show_progress(worker.runner, worker.progress)
    worker.storage = Storage(ctx.job, ctx.config, worker.runner, source_required=not ctx.rebalancing)
    with tempfile.TemporaryDirectory(prefix=prefix("batch"), dir=ctx.config.temp_dir) as temp:
        check_disks(ctx.job, config=ctx.config, check_source=not ctx.rebalancing)
        # After an interruption, never start compression or an upload whose
        # result would be discarded: each would leave an orphan at the destination.
        _check_cancelled(worker.runner)
        archive = compress(worker, item, Path(temp))
        compressed = (fingerprint(ctx.source, item.files[0].path) if item.passthrough
                      else fingerprint(Path(temp), archive.name))
        control = item.control()
        control.update(archive_size=compressed.size, archive_md5=compressed.md5)
        control_path = Path(temp) / "control.json"
        atomic_json(control_path, control)
        show_progress(worker.runner, worker.progress.scope(0.7, 0.95) if worker.progress else None)
        _check_cancelled(worker.runner)
        transferred = worker.storage.put(archive, item.key)
        events = [("transferred" if transferred else "identical", {
            "path": item.key, "files": [f.path for f in item.files],
            "bytes": compressed.size if transferred else 0})]
        show_progress(worker.runner, worker.progress.scope(0.95, 1) if worker.progress else None)
        _check_cancelled(worker.runner)
        transferred = worker.storage.put(control_path, item.key + ".json", standard="control")
        events.append(("transferred" if transferred else "identical", {"path": item.key + ".json"}))
        write_control(ctx.config, ctx.job, item.key, control)
        if not ctx.rebalancing:
            journal.record(ctx, item, control)
        return control, events


def _plan_batches(ctx):
    confirmed = {**ctx.state.get("excluded", {}), **ctx.state["active"]}
    destination_times = {}
    for control in confirmed.values():
        for item in control["files"]:
            destination_times[item["path"]] = max(destination_times.get(item["path"], -1), item["mtime_ns"])
    protect_newer(ctx.files, destination_times, ctx.event)
    next_state = copy.deepcopy(ctx.state)
    old = {}
    selected = ctx.selection.include_paths(f["path"] for c in confirmed.values() for f in c["files"])
    next_state["excluded"] = {} if not ctx.rebalancing else next_state.get("excluded", {})
    for key, control in (ctx.state["active"] if ctx.rebalancing else confirmed).items():
        if not ctx.rebalancing and any(f["path"] not in selected for f in control["files"]):
            next_state["excluded"][key] = control
            ctx.event("excluded_batch", archive=key, files=[f["path"] for f in control["files"]])
        else:
            old[key] = control
    current_files = {f.path: f.metadata() for f in ctx.files}
    # Archives stored by an interrupted earlier run come first, so the same
    # members form the same batch again and its upload is skipped (_pending_uploads).
    ctx.journal = {} if ctx.rebalancing else journal.usable(ctx, confirmed, current_files)
    previous = {**{key: journal.entry_control(entry) for key, entry in ctx.journal.items()}, **old}
    batches = plan(ctx.files, ctx.job.max_bytes, extension=ctx.job.archive_format, previous=previous,
                   individual_percent=ctx.job.individual_batch_percent, rebalance=ctx.rebalancing)
    active = {b.key: b.control() for b in batches}
    # Content-addressed archive keys allow old lots to remain in place, avoiding
    # server-side copies/restores of GLACIER and DEEP_ARCHIVE objects.
    for key, control in old.items():
        if key in active:
            continue
        deleted = any(f["path"] not in current_files for f in control["files"])
        changed = any(f["path"] in current_files and current_files[f["path"]] != f for f in control["files"])
        days = max(ctx.job.deleted_days if deleted else 0,
                   ctx.job.version_days if changed or ctx.rebalancing else 0)
        entry = retirement(key, [f["path"] for f in control["files"]],
                           "deleted" if deleted else "version", ctx.now, days, key + ".json")
        next_state["retired"].append(entry)
        ctx.scheduled(entry)
        ctx.event("would_retire" if ctx.job.dry_run else "retired", path=key, days=days)
    # Reactivated lots must never be deleted by their former retirement record.
    next_state["retired"] = [r for r in next_state["retired"] if r["key"] not in active]
    return batches, old, active, next_state


def _available(ctx, item, control, entries=None):
    """Repair a missing control without overwriting a healthy immutable archive."""
    if ctx.job.type == 'local':
        if path_has_symlink(ctx.job.destination, item.key):
            return False
        archive = Path(ctx.job.destination) / item.key
        if not archive.is_file():
            return False
        size = archive.stat().st_size
        control_path = Path(ctx.job.destination) / (item.key + '.json')
        has_control = control_path.is_file() and not path_has_symlink(ctx.job.destination, item.key + '.json')
    else:
        if item.key not in entries:
            return False
        size = entries[item.key]
        has_control = item.key + '.json' in entries
    if 'archive_size' in control and size != control['archive_size']:
        return False
    if not has_control:
        ctx.event('would_repair_control' if ctx.job.dry_run else 'repair_control', path=item.key + '.json')
        if not ctx.job.dry_run:
            with tempfile.TemporaryDirectory(prefix=prefix('control'), dir=ctx.config.temp_dir) as temp:
                path = Path(temp) / 'control.json'
                atomic_json(path, control)
                ctx.storage.put(path, item.key + '.json', standard="control")
    return True


def _pending_uploads(ctx, batches, old, active):
    retired_keys = {entry["key"] for entry in ctx.state["retired"]}
    pending = []
    from ..index_generations import listing
    entries = listing(ctx.storage) if ctx.job.type == 's3' and (old or retired_keys or ctx.journal) else None
    for item in batches:
        mirror = ctx.controls_directory / (item.key + ".json")
        saved_control = json.loads(mirror.read_text()) if item.key in retired_keys and mirror.is_file() else None
        reusable = saved_control is not None and all(saved_control.get(k) == item.control()[k]
                                                     for k in ("schema", "archive", "files"))
        if reusable:
            active[item.key] = saved_control
        journaled = item.key in ctx.journal and item.key not in old and not reusable
        available = (item.key in old or reusable or journaled) and _available(ctx, item, active[item.key], entries)
        if journaled:
            # Trust the journal only while the destination still holds the archive.
            if available:
                ctx.event("journal_reused", archive=item.key)
            else:
                journal.discard(ctx, item.key)
                ctx.event("journal_entry_discarded", archive=item.key, reason="missing")
        if available:
            for f in item.files:
                ctx.event("identical", path=f.path, archive=item.key)
            continue
        ctx.event("would_batch" if ctx.job.dry_run else "batch_start",
                  archive=item.key, files=[f.path for f in item.files], bytes=sum(f.size for f in item.files),
                  compression_profile=settings(ctx.job, item.files)[0])
        if ctx.job.dry_run:
            continue
        pending.append(item)
    return pending


def _upload_pending(ctx, pending, active):
    if pending:
        # A dedicated oversized batch can consume a whole alternate disk by
        # itself. Do not overlap it with any other staging/archive file.
        oversized = any(sum(file.size for file in item.files) > ctx.job.max_bytes for item in pending)
        workers = 1 if oversized else min(ctx.job.batch_workers, len(pending))
        # Check the largest possible simultaneous staging demand, including
        # dedicated files and existing groups exceeding the configured limit.
        largest = sorted(((temporary_bytes(item), item) for item in pending),
                         key=lambda entry: entry[0], reverse=True)[:workers]
        required = sum(size for size, _ in largest)
        try:
            check_disk(get_temporary_path(ctx.config), "temporários", ctx.event, required_bytes=required)
        except BackupError as exc:
            if "Espaço insuficiente" not in str(exc):
                raise
            details = []
            for staging_bytes, item in largest:
                files = sorted(item.files, key=lambda file: file.size, reverse=True)
                largest_files = ", ".join(f"{file.path} ({file.size} bytes)" for file in files[:3])
                if len(files) > 3:
                    largest_files += f", +{len(files) - 3} arquivos"
                details.append(f"{item.key}: staging estimado {staging_bytes} bytes; "
                               f"originais {sum(file.size for file in item.files)} bytes; "
                               f"maiores arquivos: {largest_files}")
            raise BackupError(f"{exc}; lotes que definem a estimativa: " + " | ".join(details)) from exc
        scopes = (ctx.progress.scope(0.1, 0.85).parallel(
            [sum(max(1, f.size) for f in item.files) for item in pending]) if ctx.progress else [])
        ctx.batch_progress = {item.key: progress for item, progress in zip(pending, scopes, strict=False)}
        stopped = threading.Event()

        def upload(item):
            try:
                return upload_batch(ctx, item)
            except BaseException:
                # Mark failure inside the worker before it accepts more work.
                stopped.set()
                raise

        remaining = iter(pending)
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {}
        try:
            while True:
                while len(futures) < workers and not stopped.is_set():
                    item = next(remaining, None)
                    if item is None:
                        break
                    futures[pool.submit(upload, item)] = item
                if not futures:
                    break
                # A bounded wait lets a signal delivered to another thread
                # reach this one (handlers only run in the main thread).
                done, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                # Consume every completed result before submitting replacements.
                for future in done:
                    item = futures.pop(future)
                    control, events = future.result()
                    active[item.key] = control
                    for event, values in events:
                        ctx.event(event, **values)
        except BaseException as exc:
            stopped.set()
            # Ctrl+C/SIGTERM: kill in-flight 7-Zip/rclone children so workers
            # bail out in seconds instead of finishing hours of discarded work.
            # A worker's own BackupError keeps letting its siblings finish.
            if not isinstance(exc, Exception) and isinstance(ctx.runner, Runner):
                ctx.runner.cancel()
            raise
        finally:
            stopped.set()
            # In-flight workers finish their own temporary-directory cleanup.
            pool.shutdown(wait=True, cancel_futures=True)


def batch(ctx: Context):
    """Plan, upload, publish, commit, then expire retired archives in that order."""
    batches, old, active, next_state = _plan_batches(ctx)
    if not ctx.job.dry_run:
        for key, control in {**ctx.state.get("excluded", {}), **old}.items():
            cached_control(ctx.config, ctx.job, key, control)
    pending = _pending_uploads(ctx, batches, old, active)
    _upload_pending(ctx, pending, active)
    next_state["active"] = active
    if (ctx.state_path.exists() and next_state == ctx.state and not pending and not ctx.inventory_changed
            and not next_state.get("index_pending") and not any(r["expires"] <= ctx.now for r in next_state["retired"])):
        if ctx.job.prune_orphans:
            orphans.report(ctx)
        return
    if ctx.job.dry_run:
        ctx.state = next_state
        ctx.expire()
        ctx.event("would_transfer", path=CONTROL)
        return
    verifying = [f for item in pending for f in item.files] if ctx.rebalancing else ctx.files
    verify(ctx.source, verifying, mode=ctx.job.verification_mode,
           progress=ctx.progress.scope(0.85, 0.9) if ctx.progress else None,
           verify_source_changes=ctx.job.verify_source_changes)
    show_progress(ctx.runner, ctx.progress.scope(0.9, 1) if ctx.progress else None)
    # Publish the recovery index before the local commit. If upload fails, the
    # previous local state stays authoritative; uploads can safely be repeated.
    upload_index(ctx, next_state)
    next_state.pop("index_pending", None)
    ctx.state = next_state
    ctx.save()
    journal.clear(ctx)
    from ..index_generations import finish
    finish(ctx)
    before_cleanup = copy.deepcopy(ctx.state)
    ctx.expire()
    if ctx.state != before_cleanup:
        ctx.state["index_pending"] = True
    ctx.save()
    # Publish cleanup only when retention actually removed a control entry.
    if ctx.state != before_cleanup:
        upload_index(ctx, ctx.state)
        ctx.state.pop("index_pending", None)
        ctx.save()
        finish(ctx)
    orphans.report(ctx)
