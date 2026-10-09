"""Backup orchestration, extensible strategies and retention transactions."""
from contextlib import nullcontext
import copy
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time
import uuid

from .batches import plan
from .config import Config, Job
from .errors import BackupError
from .filtering import Selection, retired_paths, frozen_rules
from .inventory import scan, verify
from .logging import RunLog
from .notify import notify
from .process import Runner
from .protection import protect_newer, timestamp_ns
from .retention import DeletionForecast, retirement, alert_time, date
from .state import atomic_json, load_state, lock
from .storage import Storage

DAY = 86400


class Context:
    def __init__(self, config, job, runner, log, files, now, selected_dirs=None, forecast=None):
        self.config, self.job, self.runner, self.log = config, job, runner, log
        self.files, self.now = files, now
        self.selection = Selection(job.exclude_files, config, runner)
        self.selected_dirs = selected_dirs
        self.forecast = forecast
        self.storage = Storage(job, config, runner)
        self.directory = config.state_dir / job.name
        self.state_path = self.directory / "state.json"
        self.identity = {"source": str(job.source), "destination": job.destination,
                         "type": job.type, "logic": job.logic, "namespace": ".sambinha"}
        self.state = load_state(self.state_path, self.identity)

    def event(self, event, **values):
        self.log.event(event, job=self.job.name, **values)

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


def preserve_metadata(ctx, item):
    if ctx.job.type == "local" and not ctx.config.dry_run:
        target = Path(ctx.storage.path(item.path))
        shutil.copystat(ctx.job.source / item.path, target, follow_symlinks=False)


def simple(ctx: Context):
    destination = ctx.storage.inventory()
    source = {f.path: f for f in ctx.files}
    protect_newer(ctx.files, {key: timestamp_ns(item.get("ModTime"))
                             for key, item in destination.items() if key in source}, ctx.event)
    # Before deleting or replacing a current file, preserve its previous bytes
    # and persist the retirement record. Interrupted retries cannot lose it.
    for key, remote in sorted(destination.items()):
        item = source.get(key)
        md5 = remote.get("Hashes", {}).get("md5") or remote.get("Hashes", {}).get("MD5")
        if item and not md5:
            raise BackupError(f"Destino não fornece checksum MD5: {key}; comparação segura impossível")
        identical = item and remote["Size"] == item.size and md5 == item.md5
        if identical:
            ctx.event("identical", path=key)
            preserve_metadata(ctx, item)
            continue
        reason = "version" if item else "deleted"
        days = ctx.config.version_days if item else ctx.config.deleted_days
        history = f".sambinha/history/{reason}/{uuid.uuid4().hex}/{key}"
        ctx.event("would_preserve" if ctx.config.dry_run else "preserve", path=key, reason=reason)
        if not ctx.config.dry_run:
            ctx.storage.preserve(key, history)
            entry = retirement(history, [key], reason, ctx.now, days)
            ctx.state["retired"].append(entry)
            ctx.scheduled(entry)
            ctx.save()
            if not item:
                ctx.storage.delete(key)
    if ctx.job.type == "local" and not ctx.config.dry_run and not ctx.job.exclude_files:
        for directory, _, _ in os.walk(ctx.job.destination, topdown=False):
            path = Path(directory)
            relative = path.relative_to(ctx.job.destination)
            if ".sambinha" not in relative.parts and not (ctx.job.source / relative).is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    for item in ctx.files:
        remote = destination.get(item.path, {})
        hashes = remote.get("Hashes", {})
        if remote.get("Size") == item.size and (hashes.get("md5") or hashes.get("MD5")) == item.md5:
            continue
        ctx.event("would_transfer" if ctx.config.dry_run else "transfer_start", path=item.path)
        if not ctx.config.dry_run:
            verify(ctx.job.source, [item])
            transferred = ctx.storage.put(ctx.job.source / item.path, item.path,
                                          protected_mtime_ns=item.mtime_ns)
            verify(ctx.job.source, [item])
            preserve_metadata(ctx, item)
            ctx.event("transferred" if transferred else "identical", path=item.path)
    # rclone transfers bytes; local copystat additionally preserves permissions
    # and modification times, including empty directories (without root).
    if ctx.job.type == "local" and not ctx.config.dry_run:
        directories = ([ctx.job.source] + sorted(ctx.job.source / p for p in ctx.selected_dirs)
                       if ctx.selected_dirs is not None else
                       [ctx.job.source] + sorted(p for p in ctx.job.source.rglob("*") if p.is_dir()))
        for directory in reversed(directories):
            relative = directory.relative_to(ctx.job.source)
            target = Path(ctx.job.destination) / relative
            target.mkdir(parents=True, exist_ok=True)
            shutil.copystat(directory, target, follow_symlinks=False)
    ctx.state["active"] = {f.path: f.metadata() for f in ctx.files}
    ctx.expire()
    if not ctx.config.dry_run:
        ctx.save()


def compress(ctx, batch, work: Path):
    snapshot = work / "snapshot"
    snapshot.mkdir()
    for item in batch.files:
        source, target = ctx.job.source / item.path, snapshot / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    verify(snapshot, list(batch.files))
    verify(ctx.job.source, list(batch.files))
    archive = work / ("archive." + ctx.job.archive_format)
    listing = work / "files.txt"
    listing.write_text("".join(f"./{f.path}\n" for f in batch.files), encoding="utf-8")
    # @listfile and -spd prevent option/wildcard interpretation of filenames.
    ctx.runner.run([ctx.job.compressor, "a", "-t" + ctx.job.archive_format,
                    "-scsUTF-8", "-spd", "-spf", str(archive), "@" + str(listing)], cwd=snapshot)
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


def batch(ctx: Context):
    confirmed = {**ctx.state.get("excluded", {}), **ctx.state["active"]}
    destination_times = {}
    for control in confirmed.values():
        for item in control["files"]:
            destination_times[item["path"]] = max(destination_times.get(item["path"], -1), item["mtime_ns"])
    protect_newer(ctx.files, destination_times, ctx.event)
    batches = plan(ctx.files, ctx.job.max_bytes, ctx.job.max_files, ctx.job.archive_format)
    next_state = copy.deepcopy(ctx.state)
    active = {b.key: b.control() for b in batches}
    old = {}
    selected = ctx.selection.include_paths(f["path"] for c in confirmed.values() for f in c["files"])
    next_state["excluded"] = {}
    for key, control in confirmed.items():
        if any(f["path"] not in selected for f in control["files"]):
            next_state["excluded"][key] = control
            ctx.event("excluded_batch", archive=key, files=[f["path"] for f in control["files"]])
        else:
            old[key] = control
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
    for item in batches:
        mirror = ctx.directory / "controls" / (item.key + ".json")
        reusable = item.key in retired_keys and mirror.is_file() and json.loads(mirror.read_text()) == item.control()
        if item.key in old or reusable:
            for f in item.files:
                ctx.event("identical", path=f.path, archive=item.key)
            continue
        ctx.event("would_batch" if ctx.config.dry_run else "batch_start",
                  archive=item.key, files=[f.path for f in item.files], bytes=sum(f.size for f in item.files))
        if ctx.config.dry_run:
            continue
        # Temporary archives disappear on success AND failure. The local mirror
        # retains only small, human-readable JSON control files.
        with tempfile.TemporaryDirectory(prefix="sambinha-batch-") as temp:
            archive = compress(ctx, item, Path(temp))
            control_path = Path(temp) / "control.json"
            atomic_json(control_path, item.control())
            transferred = ctx.storage.put(archive, item.key)
            ctx.event("transferred" if transferred else "identical", path=item.key, files=[f.path for f in item.files])
            transferred = ctx.storage.put(control_path, item.key + ".json", standard=True)
            ctx.event("transferred" if transferred else "identical", path=item.key + ".json")
            atomic_json(ctx.directory / "controls" / (item.key + ".json"), item.control())
    next_state["active"] = active
    if ctx.state_path.exists() and next_state == ctx.state and not next_state.get("index_pending") and not any(r["expires"] <= ctx.now for r in next_state["retired"]):
        return
    if ctx.config.dry_run:
        ctx.state = next_state
        ctx.expire()
        ctx.event("would_transfer", path=".sambinha/control.tar.gz")
        return
    verify(ctx.job.source, ctx.files)
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


STRATEGIES = {"simple": simple, "batch": batch}


def preflight(config, runner):
    runner.require([config.rclone] + [j.compressor for j in config.jobs if j.logic == "batch"])
    runner.run([config.rclone, "version"])
    if config.rclone_config and not Path(config.rclone_config).is_file():
        raise BackupError("Arquivo de configuração rclone não existe")
    for job in config.jobs:
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
                for directory, dirs, files in os.walk(target):
                    for name in dirs + files:
                        if (Path(directory) / name).is_symlink():
                            raise BackupError("Destino contém link simbólico; operação interrompida")
        else:
            # Verify the configured remote is actually an S3 backend before
            # writing; credentials are left to rclone's normal providers.
            raw = runner.run([config.rclone, "config", "dump", *(
                ["--config", config.rclone_config] if config.rclone_config else [])])
            remotes = json.loads(raw)
            remote = job.destination.split(":", 1)[0]
            environment = os.environ if config.environment is None else config.environment
            backend = environment.get("RCLONE_CONFIG_" + remote.upper() + "_TYPE",
                                     remotes.get(remote, {}).get("type", ""))
            if backend != "s3":
                raise BackupError(f"Remote {remote} precisa ser do tipo s3")
    inventories = {}
    for job in config.jobs:
        selected_files, selected_dirs = Selection(job.exclude_files, config, runner).source(job.source)
        inventories[job.name] = (scan(job.source, selected_files, selected_dirs), selected_dirs)
    return inventories


def run(config: Config, *, runner=None, notifier=notify, clock=time.time) -> int:
    runner = runner or Runner(config.timeout, env=config.environment)
    log = RunLog(config.log_dir)
    failed = False
    errors = []
    def telemetry(state, message=""):
        nonlocal failed
        if config.dry_run:
            return
        try:
            if message:
                notifier(config.cronitor_url, state, log.id, message[:2000])
            else:
                notifier(config.cronitor_url, state, log.id)
        except BackupError as exc:
            failed = True
            log.event("notification_error", error=str(exc))
    forecast = DeletionForecast(config.deletion_alert_threshold, log,
                                lambda message: telemetry("fail", message))
    log.event("start", dry_run=config.dry_run, sources=[str(j.source) for j in config.jobs])
    try:
        with nullcontext() if config.dry_run else lock(config.state_dir):
            telemetry("run")
            with frozen_rules(config) as effective:
                inventories = preflight(effective, runner)
                contexts = []
                for job in effective.jobs:
                    log.event("directory", job=job.name, source=str(job.source), destination=job.destination)
                    try:
                        files, selected_dirs = inventories[job.name]
                        context = Context(effective, job, runner, log, files, clock(), selected_dirs, forecast)
                        context.forecast_deletions()
                        contexts.append(context)
                    except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
                        failed = True
                        errors.append(f"{job.name}: {exc}")
                        log.event("error", job=job.name, error=str(exc))
                for context in contexts:
                    try:
                        STRATEGIES[context.job.logic](context)
                    except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
                        failed = True
                        errors.append(f"{context.job.name}: {exc}")
                        log.event("error", job=context.job.name, error=str(exc))
            if failed:
                telemetry("fail", "; ".join(errors + ([forecast.message] if forecast.sent else [])))
            elif not forecast.sent:
                telemetry("complete")
    except (BackupError, OSError, ValueError) as exc:
        failed = True
        log.event("error", error=str(exc))
        telemetry("fail", str(exc))
    finally:
        log.event("end", success=not failed, deletion_alert=forecast.sent)
        log.close()
    return 1 if failed else 0
