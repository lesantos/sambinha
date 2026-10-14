"""Immutable batch uploads and transactional recovery-index publication."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import tarfile
import tempfile

from ..archives import compress
from ..batches import plan
from ..context import Context
from ..disks import check_disks
from ..inventory import fingerprint, path_has_symlink, verify
from ..process import show_progress
from ..profiles import category, settings
from ..protection import protect_newer
from ..retention import retirement
from ..state import atomic_json
from ..storage import Storage


def upload_index(ctx, state):
    # The recovery archive includes the index AND all active/retired controls.
    # Index files always use STANDARD, allowing recovery without Glacier restore.
    with tempfile.TemporaryDirectory(prefix="sambinha-index-") as temp:
        work = Path(temp)
        recovery_state = {k: v for k, v in state.items() if k != "index_pending"}
        atomic_json(work / "state.json", recovery_state)
        archive = work / "control.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(work / "state.json", arcname="state.json")
            controls = ctx.directory / "controls"
            if controls.exists():
                tar.add(controls, arcname="controls")
        transferred = ctx.storage.put(archive, ".sambinha/control.tar.gz", standard=True)
        ctx.event("transferred" if transferred else "identical", path=".sambinha/control.tar.gz")


def upload_batch(ctx, item):
    # Each worker owns its runner's last_stderr and its temporary archive.
    worker = copy.copy(ctx)
    worker.runner = copy.copy(ctx.runner)
    worker.progress = ctx.batch_progress.get(item.key) if ctx.progress else None
    show_progress(worker.runner, worker.progress)
    worker.storage = Storage(ctx.job, ctx.config, worker.runner)
    with tempfile.TemporaryDirectory(prefix="sambinha-batch-") as temp:
        check_disks(ctx.job)
        archive = compress(worker, item, Path(temp))
        compressed = fingerprint(Path(temp), archive.name)
        control = item.control()
        control.update(archive_size=compressed.size, archive_md5=compressed.md5)
        control_path = Path(temp) / "control.json"
        atomic_json(control_path, control)
        show_progress(worker.runner, worker.progress.scope(0.7, 0.95) if worker.progress else None)
        transferred = worker.storage.put(archive, item.key)
        events = [("transferred" if transferred else "identical", {
            "path": item.key, "files": [f.path for f in item.files],
            "bytes": compressed.size if transferred else 0})]
        show_progress(worker.runner, worker.progress.scope(0.95, 1) if worker.progress else None)
        transferred = worker.storage.put(control_path, item.key + ".json", standard=True)
        events.append(("transferred" if transferred else "identical", {"path": item.key + ".json"}))
        atomic_json(ctx.directory / "controls" / (item.key + ".json"), control)
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
    next_state["excluded"] = {}
    for key, control in confirmed.items():
        if any(f["path"] not in selected for f in control["files"]):
            next_state["excluded"][key] = control
            ctx.event("excluded_batch", archive=key, files=[f["path"] for f in control["files"]])
        else:
            old[key] = control
    batches = plan(ctx.files, ctx.job.max_bytes, ctx.job.max_files,
                   ctx.job.archive_format, previous=old, category=category)
    active = {b.key: b.control() for b in batches}
    current_files = {f.path: f.metadata() for f in ctx.files}
    # Content-addressed archive keys allow old lots to remain in place, avoiding
    # server-side copies/restores of GLACIER and DEEP_ARCHIVE objects.
    for key, control in old.items():
        if key in active:
            continue
        deleted = any(f["path"] not in current_files for f in control["files"])
        changed = any(f["path"] in current_files and current_files[f["path"]] != f for f in control["files"])
        days = max(ctx.config.deleted_days if deleted else 0,
                   ctx.config.version_days if changed else 0)
        entry = retirement(key, [f["path"] for f in control["files"]],
                           "deleted" if deleted else "version", ctx.now, days, key + ".json")
        next_state["retired"].append(entry)
        ctx.scheduled(entry)
        ctx.event("would_retire" if ctx.config.dry_run else "retired", path=key, days=days)
    # Reactivated lots must never be deleted by their former retirement record.
    next_state["retired"] = [r for r in next_state["retired"] if r["key"] not in active]
    return batches, old, active, next_state


def _pending_uploads(ctx, batches, old, active):
    retired_keys = {entry["key"] for entry in ctx.state["retired"]}
    pending = []
    for item in batches:
        mirror = ctx.directory / "controls" / (item.key + ".json")
        saved_control = json.loads(mirror.read_text()) if item.key in retired_keys and mirror.is_file() else None
        reusable = saved_control is not None and all(saved_control.get(k) == item.control()[k]
                                                     for k in ("schema", "archive", "files"))
        if reusable:
            active[item.key] = saved_control
        destination_link = (ctx.job.type == "local" and
                            any(path_has_symlink(ctx.job.destination, key)
                                for key in (item.key, item.key + ".json")))
        if (item.key in old or reusable) and not destination_link:
            for f in item.files:
                ctx.event("identical", path=f.path, archive=item.key)
            continue
        ctx.event("would_batch" if ctx.config.dry_run else "batch_start",
                  archive=item.key, files=[f.path for f in item.files], bytes=sum(f.size for f in item.files),
                  compression_profile=settings(ctx.job, item.files)[0])
        if ctx.config.dry_run:
            continue
        pending.append(item)
    return pending


def _upload_pending(ctx, pending, active):
    if pending:
        scopes = (ctx.progress.scope(0.1, 0.85).parallel(
            [sum(max(1, f.size) for f in item.files) for item in pending]) if ctx.progress else [])
        ctx.batch_progress = {item.key: progress for item, progress in zip(pending, scopes)}
        with ThreadPoolExecutor(max_workers=ctx.config.batch_workers) as pool:
            futures = [(item, pool.submit(upload_batch, ctx, item)) for item in pending]
            for item, future in futures:
                control, events = future.result()
                active[item.key] = control
                for event, values in events:
                    ctx.event(event, **values)


def batch(ctx: Context):
    """Plan, upload, publish, commit, then expire retired archives in that order."""
    batches, old, active, next_state = _plan_batches(ctx)
    pending = _pending_uploads(ctx, batches, old, active)
    _upload_pending(ctx, pending, active)
    next_state["active"] = active
    if (ctx.state_path.exists() and next_state == ctx.state and not pending and not ctx.inventory_changed
            and not next_state.get("index_pending") and not any(r["expires"] <= ctx.now for r in next_state["retired"])):
        return
    if ctx.config.dry_run:
        ctx.state = next_state
        ctx.expire()
        ctx.event("would_transfer", path=".sambinha/control.tar.gz")
        return
    verify(ctx.job.source, ctx.files, mode=ctx.config.verification_mode,
           progress=ctx.progress.scope(0.85, 0.9) if ctx.progress else None)
    show_progress(ctx.runner, ctx.progress.scope(0.9, 1) if ctx.progress else None)
    # Publish the recovery index before the local commit. If upload fails, the
    # previous local state stays authoritative; uploads can safely be repeated.
    upload_index(ctx, next_state)
    next_state.pop("index_pending", None)
    ctx.state = next_state
    ctx.save()
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
