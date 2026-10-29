"""Immutable batch uploads and transactional recovery-index publication."""
import copy
import json
import re
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace

from .. import journal, orphans
from ..archives import compress, temporary_bytes
from ..batches import plan
from ..context import Context
from ..disks import check_disk, check_disks, get_temporary_path
from ..errors import BackupError
from ..fresh import requested
from ..inventory import fingerprint, path_has_symlink, verify
from ..metadata import cached_control, read_control, write_control
from ..names import CONTROL
from ..process import Runner, show_progress
from ..profiles import plan_groups, summary
from ..protection import protect_newer
from ..retention import retirement
from ..state import atomic_json
from ..tempdirs import prefix


def upload_index(ctx, state):
    from ..index_generations import publish
    publish(ctx, state)


def _check_cancelled(runner):
    # Test doubles may stand in for the runner; only a real token is consulted.
    if isinstance(runner, Runner):
        runner.check_cancelled()


def _known_passthrough(ctx, item):
    """Size and md5 an earlier upload of this very file recorded, or None when it must be hashed again.

    The local mirror is written only after the archive and its control were stored, and is trusted
    only while every member stat (path, size, mtime, mode) still matches, the same evidence simple
    mode relies on for the source. Anything odd falls back to reading the file.
    """
    mirror = ctx.controls_directory / (item.key + ".json")
    try:
        control = read_control(mirror, item.key) if mirror.is_file() else None
    except BackupError:
        return None
    if (control is None or not control.get("passthrough") or control.get("archive") != item.key
            or control.get("files") != item.control()["files"] or len(item.files) != 1
            or control.get("archive_size") != item.files[0].size):
        return None
    return SimpleNamespace(size=control["archive_size"], md5=control["archive_md5"])


def _stored_md5(worker, key, path):
    """Size and md5 of the S3 object just stored, from its metadata, or None to hash ``path`` instead.

    rclone computes the md5 of what it sends and keeps it on the object (also for multipart uploads), so a
    HEAD answers without reading the file again. Local destinations would make rclone read the stored
    copy to hash it, and an object without a usable md5 or with another size is not evidence: both fall back.
    """
    if worker.job.type != "s3":
        return None
    try:
        stored = worker.storage.stat(key)
    except BackupError:
        return None
    md5 = (stored.get("Hashes") or {}).get("md5")
    if (not isinstance(md5, str) or not re.fullmatch("[0-9a-f]{32}", md5)
            or stored["Size"] != path.stat().st_size):
        return None
    return SimpleNamespace(size=stored["Size"], md5=md5)


def upload_batch(ctx, item):
    # Each worker owns its runner's last_stderr and its temporary archive.
    # The runner copy shares the cancellation token (see process.Runner); the view shares the rest of ctx.
    worker = ctx.worker_view(copy.copy(ctx.runner), ctx.batch_progress.get(item.key) if ctx.progress else None)
    show_progress(worker.runner, worker.progress)
    with tempfile.TemporaryDirectory(prefix=prefix("batch"), dir=ctx.config.temp_dir) as temp:
        check_disks(ctx.job, config=ctx.config, check_source=not ctx.rebalancing)
        # After an interruption, never start compression or an upload whose
        # result would be discarded: each would leave an orphan at the destination.
        _check_cancelled(worker.runner)
        archive = compress(worker, item, Path(temp))
        # Only the archive size and md5 are recorded; sha256 of the archive is never used.
        # The md5 comes from the control mirror of an earlier upload of this very file, else from the
        # object rclone just stored (it hashed the bytes it sent), else from reading the file again.
        known = _known_passthrough(ctx, item) if item.passthrough else None
        if known:
            ctx.event("md5_reused", archive=item.key)
        show_progress(worker.runner, worker.progress.scope(0.7, 0.95) if worker.progress else None)
        _check_cancelled(worker.runner)
        transferred = (worker.storage.put(archive, item.key, metadata_comparison=True) if item.passthrough
                       else worker.storage.put(archive, item.key))
        compressed = known or _stored_md5(worker, item.key, archive)
        if compressed:
            if not known:
                ctx.event("md5_from_destination", archive=item.key)
        else:
            compressed = (fingerprint(ctx.source, item.files[0].path, digests={"md5"}) if item.passthrough
                          else fingerprint(archive.parent, archive.name, digests={"md5"}))
        control = item.control()
        control.update(archive_size=compressed.size, archive_md5=compressed.md5)
        control_path = Path(temp) / "control.json"
        atomic_json(control_path, control)
        events = [("transferred" if transferred else "identical", {
            "path": item.key, "files": [f.path for f in item.files],
            "bytes": compressed.size if transferred else 0})]
        show_progress(worker.runner, worker.progress.scope(0.95, 1) if worker.progress else None)
        _check_cancelled(worker.runner)
        transferred = worker.storage.put(control_path, item.key + ".json", standard="control")
        events.append(("transferred" if transferred else "identical", {"path": item.key + ".json"}))
        write_control(ctx.config, ctx.job, item.key, control, memo=ctx.control_memo)
        if not ctx.rebalancing:
            journal.record(ctx, item, control)
        return control, events


def snapshot(state):
    """Independent copy of what a run mutates: containers and per-entry dicts, sharing the immutable controls.

    Retired entries are edited in place (``control`` popped when its mirror is gone), the
    ``active``/``excluded`` maps and the ``retired``/``origins`` lists are reassigned or appended
    to; controls themselves are never modified after creation.
    """
    result = dict(state)
    for key, value in state.items():
        if isinstance(value, dict):
            result[key] = dict(value)
        elif isinstance(value, list):
            result[key] = [dict(item) if isinstance(item, dict) else item for item in value]
    return result


def _plan_batches(ctx):
    ctx.step("Planejando lotes", 1)
    confirmed = {**ctx.state.get("excluded", {}), **ctx.state["active"]}
    destination_times = {}
    for control in confirmed.values():
        for item in control["files"]:
            destination_times[item["path"]] = max(destination_times.get(item["path"], -1), item["mtime_ns"])
    protect_newer(ctx.files, destination_times, ctx.event)
    next_state = snapshot(ctx.state)
    old = {}
    selected = ctx.selection.include_paths((f["path"] for c in confirmed.values() for f in c["files"]),
                                           ctx.selected_paths())
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
    ctx.step("Lendo o diário de envios", 2)
    ctx.journal = {} if ctx.rebalancing or requested(ctx.job) else journal.usable(ctx, confirmed, current_files)
    ctx.step("Agrupando arquivos em lotes", 3)
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


def _adoptable(ctx, batches, old, retired_keys):
    """Keys of planned batches no confirmed state, journal or retirement accounts for, which may already be stored.

    Without a committed state every such batch qualifies (its control may be at the destination); with one,
    only those with a local control mirror do. Fresh starts and rebalances adopt nothing.
    """
    if ctx.rebalancing or requested(ctx.job):
        return set()
    lost = not ctx.state_path.exists()
    return {item.key for item in batches
            if item.key not in old and item.key not in retired_keys and item.key not in ctx.journal
            and (lost or (ctx.controls_directory / (item.key + ".json")).is_file())}


def _mirror_control(ctx, key):
    """The local control mirror of ``key`` when it is valid and records the archive size and md5."""
    mirror = ctx.controls_directory / (key + ".json")
    try:
        control = read_control(mirror, key) if mirror.is_file() else None
    except BackupError:
        return None
    return control if control and "archive_size" in control and "archive_md5" in control else None


def _stored_controls(ctx, keys, entries):
    """Controls stored at the destination next to their archives, for ``keys`` the listing shows complete.

    One bulk ``rclone copy`` of the small control objects (never of an archive) into a temporary directory.
    Unreadable or incomplete controls are left out, so their batches are simply rebuilt.
    """
    found = {}
    if ctx.job.type == "s3":
        wanted = [key for key in keys if key in entries and key + ".json" in entries]
        if not wanted:
            return found
        with tempfile.TemporaryDirectory(prefix=prefix("controls"), dir=ctx.config.temp_dir) as temp:
            ctx.storage.command("copy", ctx.job.destination, temp, "--files-from-raw", "-", "--transfers", "16",
                                retry=True, standard="control", input="".join(key + ".json\n" for key in wanted))
            for key in wanted:
                found[key] = _read_stored(Path(temp) / (key + ".json"), key)
    else:
        for key in keys:
            archive = Path(ctx.job.destination) / key
            if (archive.is_file() and not path_has_symlink(ctx.job.destination, key)
                    and not path_has_symlink(ctx.job.destination, key + ".json")):
                found[key] = _read_stored(Path(ctx.job.destination) / (key + ".json"), key)
    return {key: control for key, control in found.items() if control}


def _read_stored(path, key):
    try:
        control = read_control(path, key) if path.is_file() else None
    except BackupError:
        return None
    return control if control and "archive_size" in control and "archive_md5" in control else None


def _pending_uploads(ctx, batches, old, active):
    retired_keys = {entry["key"] for entry in ctx.state["retired"]}
    pending = []
    adopting = _adoptable(ctx, batches, old, retired_keys)
    adopted = from_destination = 0
    from ..index_generations import listing
    entries = (listing(ctx.storage) if ctx.job.type == 's3' and (old or retired_keys or ctx.journal or adopting)
               else None)
    if entries is not None:
        ctx.listing = entries  # snapshot before this run's uploads; reused by the orphan report
    mirrors = {key: _mirror_control(ctx, key) for key in adopting}
    elsewhere = [key for key, control in mirrors.items() if control is None]
    stored = _stored_controls(ctx, elsewhere, entries) if elsewhere and not ctx.state_path.exists() else {}
    for item in batches:
        mirror = ctx.controls_directory / (item.key + ".json")
        adopt = item.key in adopting
        remote = adopt and mirrors[item.key] is None
        if adopt:
            saved_control = mirrors[item.key] or stored.get(item.key)
        else:
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
            if adopt and reusable:
                adopted += 1
                if remote:
                    from_destination += 1
                    if not ctx.job.dry_run:
                        write_control(ctx.config, ctx.job, item.key, saved_control, memo=ctx.control_memo)
            for f in item.files:
                ctx.event("identical", path=f.path, archive=item.key)
            continue
        ctx.event("would_batch" if ctx.job.dry_run else "batch_start",
                  archive=item.key, files=[f.path for f in item.files], bytes=sum(f.size for f in item.files),
                  compression_profile="tiered", compression_groups=summary(plan_groups(ctx.job, item.files)))
        if ctx.job.dry_run:
            continue
        pending.append(item)
    if adopted:
        ctx.event("batches_adopted", count=adopted, from_destination=from_destination, rebuilt=len(pending))
    return pending


def _staging_check(ctx, items, workers):
    # Check the largest possible simultaneous staging demand, including
    # dedicated files and existing groups exceeding the configured limit.
    largest = sorted(((temporary_bytes(item, ctx.job), item) for item in items),
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


def _run_workers(ctx, items, workers, active):
    stopped = threading.Event()

    def upload(item):
        try:
            return upload_batch(ctx, item)
        except BaseException:
            # Mark failure inside the worker before it accepts more work.
            stopped.set()
            raise

    remaining = iter(items)
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




def _upload_pending(ctx, pending, active):
    if not pending:
        return
    # A dedicated batch above max_megabytes that is built here (anything but a stored-as-is archive, which
    # stages nothing) can consume a whole alternate disk by itself: those run alone, one at a time, after
    # the ordinary batches, which keep the configured parallelism.
    heavy = [item for item in pending if not item.passthrough
             and sum(file.size for file in item.files) > ctx.job.max_bytes]
    heavy_keys = {item.key for item in heavy}
    light = [item for item in pending if item.key not in heavy_keys]
    scopes = (ctx.progress.scope(0.1, 0.85).parallel(
        [sum(max(1, f.size) for f in item.files) for item in pending]) if ctx.progress else [])
    ctx.batch_progress = {item.key: progress for item, progress in zip(pending, scopes, strict=False)}
    for items, workers in ((light, min(ctx.job.batch_workers, len(light))), (heavy, 1)):
        if items:
            _staging_check(ctx, items, workers)
            _run_workers(ctx, items, workers, active)


def batch(ctx: Context):
    """Plan, upload, publish, commit, then expire retired archives in that order."""
    batches, old, active, next_state = _plan_batches(ctx)
    if not ctx.job.dry_run:
        for key, control in {**ctx.state.get("excluded", {}), **old}.items():
            cached_control(ctx.config, ctx.job, key, control, memo=ctx.control_memo)
    ctx.step("Conferindo lotes no destino", 4)
    pending = _pending_uploads(ctx, batches, old, active)
    ctx.step("Lotes conferidos", 5)
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
           verify_source_changes=ctx.job.verify_source_changes,
           signatures=ctx.signatures)
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
    before_cleanup = snapshot(ctx.state)
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
