"""Read-only job checks and independent batch inventories."""
import json
import os
from pathlib import Path
from typing import NamedTuple
import time

from .disks import check_disks
from .errors import BackupError, job_errors
from .filtering import Selection
from .inventory import File, scan
from .index_generations import backup_state
from .mounts import check_mounts
from .process import credential_values, show_progress
from .retention import DAY
from .versions import check_compressor_version


class JobInventory(NamedTuple):
    files: list[File]
    selected_dirs: set[str] | None
    fingerprints: dict
    last_full_scan: float
    previous: dict


def _check_destination(config, job, runner, checking):
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


def _read_inventory(config, job, runner, now, reading):
    previous = backup_state(config, job, runner)
    force = (not job.fingerprint_cache or "last_full_scan" not in previous or
             now < previous["last_full_scan"] or
             now - previous["last_full_scan"] >= job.fingerprint_full_scan_days * DAY)
    selection = Selection(job.exclude_files, config, runner)
    selected_dirs = set() if job.exclude_files else None
    fingerprints = {}
    with selection.source_listing(job.source) as listing:
        files = scan(job.source, listing=listing, selected_dirs_out=selected_dirs,
                     cache=None if force else previous.get("fingerprints"), cache_out=fingerprints,
                     mode=job.verification_mode, progress=reading,
                     verify_source_changes=job.verify_source_changes)
    return JobInventory(files, selected_dirs, fingerprints,
                        now if force else previous["last_full_scan"], previous)


def preflight(config, runner, now=None, *, progress=None, on_job_error=None):
    now = time.time() if now is None else now
    checked = set()
    for index, job in enumerate(config.jobs):
        with job_errors(job.name, on_job_error if job.continue_on_job_error else None):
            runner.require(([job.compressor] if job.logic == "batch" else [])
                           + (["mountpoint"] if job.source_mount or job.destination_mount else []))
            if job.logic == "batch":
                check_compressor_version(runner, job.compressor)
            checking = progress.scope(0.2 * index / len(config.jobs), 0.2 * (index + 1) / len(config.jobs)) if progress else None
            show_progress(runner, checking)
            if checking:
                checking.update("Verificando discos", job.source)
            check_mounts(job, runner)
            check_disks(job, config=config)
            if job.exclude_files:
                try:
                    with job.exclude_files.open(encoding="utf-8") as stream:
                        stream.read()
                except (OSError, UnicodeError):
                    raise BackupError(f"Arquivo exclude_files ausente ou ilegível: {job.name}") from None
            _check_destination(config, job, runner, checking)
            checked.add(job.name)
    inventories = {}
    for index, job in enumerate(config.jobs):
        if job.name not in checked or job.logic == "simple":
            continue
        with job_errors(job.name, on_job_error if job.continue_on_job_error else None):
            reading = progress.scope(0.2 + 0.8 * index / len(config.jobs),
                                     0.2 + 0.8 * (index + 1) / len(config.jobs)) if progress else None
            show_progress(runner, reading.scope(0, 0.1) if reading else None)
            inventories[job.name] = _read_inventory(config, job, runner, now, reading)
            if reading:
                files = inventories[job.name][0]
                reading.update("Inventário concluído", (job.source / files[-1].path).parent if files else job.source, 1, 1)
    return inventories
