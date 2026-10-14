"""Backup orchestration, extensible strategies and retention transactions."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

from .batches import plan
from .config import Config, Job
from .errors import BackupError, JobError, job_errors
from .filtering import Selection, retired_paths, frozen_rules
from .inventory import fingerprint, scan, verify, path_has_symlink
from .logging import RunLog
from .notify import notify
from .process import Runner, credential_values
from .protection import protect_newer
from .retention import DeletionForecast, retirement, alert_time, date
from .mounts import check_mounts
from .state import atomic_json, load_state, lock
from .disks import check_disks
from .profiles import category, settings
from .storage import Storage
from .progress import Progress

DAY = 86400


def show_progress(runner, progress):
    if isinstance(runner, Runner):
        runner.progress = progress


class Context:
    def __init__(self, config, job, runner, log, files, now, selected_dirs=None, forecast=None, progress=None):
        self.config, self.job, self.runner, self.log = config, job, runner, log
        self.files, self.now = files, now
        self.selection = Selection(job.exclude_files, config, runner,
                                   ignored_roots=(job.source,))
        self.selected_dirs = selected_dirs
        self.forecast = forecast
        self.progress = progress
        self.storage = Storage(job, config, runner)
        self.directory = config.state_dir / job.name
        self.state_path = self.directory / "state.json"
        self.identity = {"source": str(job.source), "destination": job.destination,
                         "type": job.type, "logic": job.logic, "namespace": ".sambinha"}
        self.state = load_state(self.state_path, self.identity)
        self.metrics = {"reused_batches": 0, "rebuilt_batches": 0,
                        "bytes_planned": 0, "bytes_transferred": 0}
        self.reused_batches = set()
        self.file_sizes = {f.path: f.size for f in files}
        self.before_files = ({p: f for p, f in self.state["active"].items()} if job.logic == "simple" else
                             {f["path"]: f for c in self.state["active"].values() for f in c["files"]})
        self.removed = []
        self.inventory_changed = False
        self.max_delete = -1

    def event(self, event, **values):
        if event in {"batch_start", "would_batch"}:
            self.metrics["rebuilt_batches"] += 1
            self.metrics["bytes_planned"] += values["bytes"]
        elif event in {"transfer_start", "would_transfer"} and values.get("path") in self.file_sizes:
            self.metrics["bytes_planned"] += self.file_sizes[values["path"]]
        elif event == "identical" and "archive" in values:
            self.reused_batches.add(values["archive"])
            self.metrics["reused_batches"] = len(self.reused_batches)
        elif event == "transferred":
            self.metrics["bytes_transferred"] += values.get("bytes", 0)
        self.log.event(event, job=self.job.name, **values)

    def protect_deletions(self, allowed=False):
        included = self.selection.include_paths(self.before_files)
        self.removed = sorted(included - self.file_sizes.keys())
        if not allowed and len(included) >= self.job.deletion_min_files:
            self.max_delete = int(len(included) * self.job.max_delete_percent / 100)
        if not allowed and self.max_delete >= 0 and len(self.removed) > self.max_delete:
            self.event("mass_deletion_blocked", removed_files=len(self.removed),
                       previous_files=len(included), threshold_percent=self.job.max_delete_percent,
                       paths=self.removed)
            raise BackupError(f"Remoções em massa bloqueadas: {len(self.removed)} de {len(included)} arquivos; "
                              "confira a origem e use --allow-mass-deletion apenas para remoções intencionais")

    def summary(self, success):
        current = {f.path: f.metadata() for f in self.files}
        self.event("summary", success=success, dry_run=self.config.dry_run,
                   total_files=len(current), new_files=len(current.keys() - self.before_files.keys()),
                   modified_files=sum(p in self.before_files and f != self.before_files[p]
                                      for p, f in current.items()), removed_files=len(self.removed),
                   unchanged_files=sum(f == self.before_files.get(p) for p, f in current.items()),
                   pending_deletions=sum(len(r.get("files", [])) for r in self.state["retired"]), **self.metrics)

    def save(self):
        atomic_json(self.state_path, self.state)

    def forecast_deletions(self):
        if self.forecast is None:
            return
        paths = {entry["key"]: retired_paths(entry, self.directory) for entry in self.state["retired"]}
        included = self.selection.include_paths(p for members in paths.values() for p in members)
        candidates = [(entry, paths[entry["key"]]) for entry in self.state["retired"]
                      if alert_time(entry, self.config.deleted_days, self.config.version_days) <= self.now
                      and all(p in included for p in paths[entry["key"]])]
        self.forecast.add(self.job.name, candidates)

    def scheduled(self, entry):
        self.event("deletion_scheduled", path=entry["key"], files=entry["files"],
                   reason=entry["reason"], deletion_at=date(entry["expires"]),
                   alert_from=date(entry["alert_at"]))

    def expire(self):
        self.forecast_deletions()
        paths = {entry["key"]: retired_paths(entry, self.directory) for entry in self.state["retired"]}
        included = self.selection.include_paths(p for members in paths.values() for p in members)
        remaining = []
        for entry in self.state["retired"]:
            if any(p not in included for p in paths[entry["key"]]):
                self.event("excluded_history", path=entry["key"], files=paths[entry["key"]])
                remaining.append(entry)
                continue
            if entry["expires"] > self.now:
                remaining.append(entry)
                continue
            self.event("would_expire" if self.config.dry_run else "expire", path=entry["key"],
                       files=paths[entry["key"]], deletion_at=date(entry["expires"]))
            if not self.config.dry_run:
                self.storage.delete(entry["key"])
                if entry.get("control"):
                    self.storage.delete(entry["control"])
                    (self.directory / "controls" / entry["control"]).unlink(missing_ok=True)
        self.state["retired"] = remaining


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


def batch(ctx: Context):
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


STRATEGIES = {"batch": batch}


def preflight(config, runner, now=None, *, progress=None, on_job_error=None):
    now = time.time() if now is None else now
    checked = set()
    for index, job in enumerate(config.jobs):
        with job_errors(job.name, on_job_error if job.continue_on_job_error else None):
            runner.require(([job.compressor] if job.logic == "batch" else [])
                           + (["mountpoint"] if job.source_mount or job.destination_mount else []))
            checking = progress.scope(0.2 * index / len(config.jobs), 0.2 * (index + 1) / len(config.jobs)) if progress else None
            show_progress(runner, checking)
            if checking:
                checking.update("Verificando discos", job.source)
            check_mounts(job, runner)
            check_disks(job)
            if job.exclude_files:
                try:
                    with job.exclude_files.open(encoding="utf-8") as stream:
                        stream.read()
                except (OSError, UnicodeError):
                    raise BackupError(f"Arquivo exclude_files ausente ou ilegível: {job.name}") from None
            if job.type == "local":
                target = Path(job.destination)
                if target.exists() and not target.is_dir():
                    raise BackupError("Destino local não é um diretório")
                if target.exists():
                    if checking:
                        checking.update("Verificando destino", target)
            else:
                # Verify the configured remote is actually an S3 backend before
                # writing; credentials are left to rclone's normal providers.
                raw = runner.run([config.rclone, "config", "dump", *(
                    ["--config", config.rclone_config] if config.rclone_config else [])])
                remotes = json.loads(raw)
                remote = job.destination.split(":", 1)[0]
                if not isinstance(remotes, dict) or not isinstance(remotes.get(remote, {}), dict):
                    raise BackupError("Configuração de remotes rclone inválida")
                runner.secrets = tuple(set(runner.secrets) | credential_values(remotes.get(remote, {})))
                environment = os.environ if config.environment is None else config.environment
                backend = environment.get("RCLONE_CONFIG_" + remote.upper() + "_TYPE",
                                         remotes.get(remote, {}).get("type", ""))
                if backend != "s3":
                    raise BackupError(f"Remote {remote} precisa ser do tipo s3")
            checked.add(job.name)
    inventories = {}
    for index, job in enumerate(config.jobs):
        if job.name not in checked or job.logic == "simple":
            continue
        with job_errors(job.name, on_job_error if job.continue_on_job_error else None):
            reading = progress.scope(0.2 + 0.8 * index / len(config.jobs),
                                     0.2 + 0.8 * (index + 1) / len(config.jobs)) if progress else None
            show_progress(runner, reading.scope(0, 0.1) if reading else None)
            try:
                from .recovery import identity
                previous = load_state(config.state_dir / job.name / "state.json", identity(job))
            except BackupError:
                # Context will reject the corrupt index for this job; no cached
                # fingerprints from it are trusted during this job's preflight.
                previous = {}
            force = (not config.fingerprint_cache or "last_full_scan" not in previous or
                     now < previous["last_full_scan"] or
                     now - previous["last_full_scan"] >= config.fingerprint_full_scan_days * DAY)
            selected_files, selected_dirs = Selection(job.exclude_files, config, runner).source(job.source)
            fingerprints = {}
            files = scan(job.source, selected_files, selected_dirs,
                         cache=None if force else previous.get("fingerprints"), cache_out=fingerprints,
                         mode=config.verification_mode, progress=reading.scope(0.1, 1) if reading else None)
            inventories[job.name] = (files, selected_dirs, fingerprints,
                                     now if force else previous["last_full_scan"])
            if reading:
                files = inventories[job.name][0]
                reading.update("Inventário concluído", (job.source / files[-1].path).parent if files else job.source, 1, 1)
    return inventories


def run(config: Config, *, runner=None, notifier=notify, clock=time.time, allow_mass_deletion=False) -> int:
    runner = runner or Runner(config.timeout, env=config.environment)
    redactor = Runner(env=config.environment, secrets=(config.cronitor_url,)).safe_error
    progress = Progress(redact=redactor)
    if isinstance(runner, Runner):
        runner.progress = progress
    log = RunLog(config.log_dir, redact=redactor, progress=progress,
                 file_level=config.log_file_level, terminal_level=config.log_terminal_level)
    failed = False
    errors = []
    def report_job_error(error):
        nonlocal failed
        failed = True
        errors.append(str(error))
        log.event("error", job=error.job, error=str(error))
    def telemetry(state, message=""):
        nonlocal failed
        if config.dry_run:
            return
        try:
            if message:
                notifier(config.cronitor_url, state, log.id, redactor(message)[:2000])
            else:
                notifier(config.cronitor_url, state, log.id)
        except BackupError as exc:
            failed = True
            log.event("notification_error", error=str(exc))
    deletion_alerts = []
    log.event("start", dry_run=config.dry_run, sources=[str(j.source) for j in config.jobs])
    try:
        runner.load_config_secrets(config.rclone_config)
        with nullcontext() if config.dry_run else lock(config.state_dir):
            if not config.dry_run:
                removed_logs = log.prune(config.log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=config.log_retention_days)
            telemetry("run")
            runner.require([config.rclone])
            runner.run([config.rclone, "version"])
            if config.rclone_config and not Path(config.rclone_config).is_file():
                raise BackupError("Arquivo de configuração rclone não existe")
            # Each job owns its full lifecycle. Later jobs must see the files,
            # rules and state left by earlier jobs, just as separate invocations do.
            for index, job in enumerate(config.jobs):
                job_progress = progress.scope(0.95 * index / len(config.jobs),
                                              0.95 * (index + 1) / len(config.jobs))
                context = None
                success = False
                forecast = DeletionForecast(config.deletion_alert_threshold, log,
                                            lambda message: telemetry("fail", message))
                log.event("directory", job=job.name, source=str(job.source), destination=job.destination)
                try:
                    with frozen_rules(replace(config, jobs=(job,))) as effective:
                        job = effective.jobs[0]
                        inventories = preflight(effective, runner, clock(), progress=job_progress.scope(0, 0.2))
                        if job.logic == "simple":
                            job_now = clock()
                            history_id = (datetime.fromtimestamp(job_now, timezone.utc).strftime("%Y%m%dT%H%M%S")
                                          + "-" + log.id)
                            backup_dir = f".sambinha/history/{history_id}"
                            max_delete = -1 if allow_mass_deletion else job.max_delete
                            log.event("sync_start", job=job.name, max_delete=max_delete,
                                      backup_dir=backup_dir)
                            show_progress(runner, job_progress.scope(0.2, 0.95))
                            storage = Storage(job, effective, runner, log=log)
                            newer = storage.sync_simple(
                                job.source, backup_dir, max_delete=max_delete, dry_run=config.dry_run)
                            retention_days = max(config.deleted_days, config.version_days)
                            expired = storage.expire_simple_history(
                                job_now, retention_days, dry_run=config.dry_run)
                            for history in expired:
                                log.event("would_expire" if config.dry_run else "expire_history",
                                          job=job.name, path=history, retention_days=retention_days)
                            if newer:
                                paths = ", ".join(newer)
                                raise BackupError("Arquivos mais novos preservados no destino; job marcado como falho "+
                                                  "para revisão manual: " + paths)
                        else:
                            files, selected_dirs, fingerprints, last_full_scan = inventories[job.name]
                            context = Context(effective, job, runner, log, files, clock(), selected_dirs, forecast,
                                              job_progress.scope(0.2, 0.9))
                            cached_inventory = fingerprints if config.fingerprint_cache else {}
                            context.inventory_changed = (context.state.get("fingerprints") != cached_inventory or
                                                         context.state.get("last_full_scan") != last_full_scan)
                            context.state["fingerprints"] = cached_inventory
                            context.state["last_full_scan"] = last_full_scan
                            context.forecast_deletions()
                            check_disks(context.job, context.event)
                            context.protect_deletions(allow_mass_deletion)
                            STRATEGIES[context.job.logic](context)
                            check_disks(context.job, context.event)
                            interval = config.verify_interval_days * DAY
                            if interval and ("last_verified" not in context.state or
                                             context.now - context.state["last_verified"] >= interval):
                                if config.dry_run:
                                    context.event("would_verify", mode="s3_metadata" if context.job.type == "s3" else "contents")
                                else:
                                    from .recovery import verify_job
                                    checking = job_progress.scope(0.9, 1)
                                    show_progress(runner, checking)
                                    verify_job(effective, context.job, runner, log, context.state, progress=checking)
                                    verified = copy.deepcopy(context.state)
                                    verified["last_verified"] = context.now
                                    context.state = verified
                                    context.save()
                    success = True
                except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
                    report_job_error(exc if isinstance(exc, JobError) else JobError(job.name, exc))
                finally:
                    if forecast.sent:
                        deletion_alerts.append(forecast.message)
                    if context is not None:
                        context.summary(success)
                    elif job.logic == "simple":
                        log.event("summary", job=job.name, success=success, dry_run=config.dry_run,
                                  strategy="rclone_sync", max_delete=-1 if allow_mass_deletion else job.max_delete)
                    if success:
                        job_progress.update("Concluindo tarefa", progress.directory, 1, 1)
                if not success and not job.continue_on_job_error:
                    break
            if failed:
                telemetry("fail", "; ".join(errors + deletion_alerts))
            elif not deletion_alerts:
                telemetry("complete")
    except KeyboardInterrupt:
        failed = True
        raise
    except (BackupError, OSError, ValueError) as exc:
        failed = True
        details = {"job": exc.job} if isinstance(exc, JobError) else {}
        log.event("error", error=str(exc), **details)
        telemetry("fail", "; ".join([*errors, str(exc)]))
    except BaseException:
        failed = True
        raise
    finally:
        log.event("end", success=not failed, deletion_alert=bool(deletion_alerts))
        if not failed:
            progress.finish()
        progress.close()
        log.close()
    return 1 if failed else 0
