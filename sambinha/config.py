"""Validated TOML configuration and command-line overrides."""
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import tomllib

from .config_validation import LOG_LEVELS as LOG_LEVELS, STORAGE_CLASSES as STORAGE_CLASSES
from .config_validation import validate as validate
from .config_locations import key_lines
from .errors import ConfigError
from .paths import destinations_overlap as destinations_overlap, overlap as overlap
from .secrets import expand, read_environment

JOB_SCOPED_OPTIONS = frozenset({"recovery_index_versions", "deleted_days", "version_days", "log_retention_days",
                                "log_file_level", "log_terminal_level", "deletion_alert_threshold",
                                "verify_interval_days", "verification_mode", "fingerprint_cache",
                                "fingerprint_full_scan_days", "verify_source_changes",
                                "transfer_workers", "batch_workers", "dry_run"})
JOB_ONLY_OPTIONS = JOB_SCOPED_OPTIONS | {"control_storage_class"}


@dataclass(frozen=True)
class Job:
    name: str
    source: Path
    destination: str
    type: str = "local"
    logic: str = "simple"
    storage_class: str = "GLACIER_IR"
    control_storage_class: str = "STANDARD"
    max_megabytes: int | float = 1024
    individual_batch_percent: float = 100
    batch_size_alert_percent: float = 200
    compressor: str = "7z"
    archive_format: str = "7z"
    exclude_files: Path | None = None
    source_mount: Path | None = None
    destination_mount: Path | None = None
    max_delete: int = 100
    max_delete_percent: float = 50
    deletion_min_files: int = 20
    check_source_disk: bool = True
    check_destination_disk: bool = True
    continue_on_job_error: bool = True
    compression_level: int = 3
    compression_threads: int = 2
    recovery_index_versions: int = 3
    deleted_days: int = 10
    version_days: int = 2
    log_retention_days: int = 14
    log_file_level: str = "summary"
    log_terminal_level: str = "summary"
    deletion_alert_threshold: int = 1000
    verify_interval_days: int = 0
    verification_mode: str = "simple"
    fingerprint_cache: bool = True
    fingerprint_full_scan_days: int = 7
    verify_source_changes: bool = True
    transfer_workers: int = 4
    batch_workers: int = 2
    dry_run: bool = False

    @property
    def max_bytes(self) -> int:
        """Convert the configured MB (1 MB = 1,048,576 bytes) for packing."""
        return int(self.max_megabytes * 1024 ** 2)


@dataclass(frozen=True)
class Config:
    jobs: tuple[Job, ...]
    state_dir: Path = field(default_factory=lambda: Path("state").resolve())
    log_dir: Path = field(default_factory=lambda: Path("logs").resolve())
    cronitor_url: str = field(default="", repr=False)
    rclone: str = "rclone"
    rclone_config: str = ""
    timeout: int = 3600
    secrets_file: Path | None = None
    environment: dict[str, str] | None = field(default=None, repr=False, compare=False)
    metadata_dir: Path | None = None
    temp_dir: Path | None = None
    # Source-compatible Python overrides. These are deliberately excluded from
    # TOML's [backup] keys; loaded values always live on their individual jobs.
    log_retention_days: int | None = field(default=None, repr=False, compare=False)
    deleted_days: int | None = field(default=None, repr=False, compare=False)
    version_days: int | None = field(default=None, repr=False, compare=False)
    dry_run: bool | None = field(default=None, repr=False, compare=False)
    deletion_alert_threshold: int | None = field(default=None, repr=False, compare=False)
    verify_interval_days: int | None = field(default=None, repr=False, compare=False)
    fingerprint_cache: bool | None = field(default=None, repr=False, compare=False)
    verification_mode: str | None = field(default=None, repr=False, compare=False)
    fingerprint_full_scan_days: int | None = field(default=None, repr=False, compare=False)
    verify_source_changes: bool | None = field(default=None, repr=False, compare=False)
    transfer_workers: int | None = field(default=None, repr=False, compare=False)
    batch_workers: int | None = field(default=None, repr=False, compare=False)
    log_file_level: str | None = field(default=None, repr=False, compare=False)
    log_terminal_level: str | None = field(default=None, repr=False, compare=False)
    recovery_index_versions: int | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        # Preserve callers that use dataclasses.replace(config, option=value),
        # while applying that invocation override explicitly to each job.
        overrides = {name: getattr(self, name) for name in JOB_SCOPED_OPTIONS
                     if getattr(self, name) is not None}
        if overrides:
            object.__setattr__(self, "jobs", tuple(replace(job, **overrides) for job in self.jobs))
            for name in overrides:
                object.__setattr__(self, name, None)


def local_path(value, base, environment=None):
    if not isinstance(value, str) or not value:
        raise ConfigError("Caminho deve ser uma string não vazia")
    path = Path(expand(value, os.environ if environment is None else environment)).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load(path: Path, overrides: dict | None = None) -> Config:
    try:
        source = path.read_bytes().decode("utf-8")
        data = tomllib.loads(source)

        def unknown_parameters(values, allowed, section, prefix):
            unknown = set(values) - allowed
            if unknown:
                locations = key_lines(source)
                details = '; '.join(
                    f'{name!r} (linha {locations[prefix + (name,)]})'
                    for name in values if name in unknown)
                raise ConfigError(f"Parâmetros desconhecidos em {section}: {details}; arquivo: {path}")

        if set(data) - {"backup", "jobs"}:
            raise ConfigError("Seções desconhecidas no TOML")
        global_values = dict(data.get("backup", {}))
        job_keys = set(Job.__dataclass_fields__)
        config_keys = set(Config.__dataclass_fields__) - {"jobs", "environment"} - JOB_SCOPED_OPTIONS
        per_job_in_backup = set(global_values) & JOB_ONLY_OPTIONS
        if per_job_in_backup:
            names = ", ".join(sorted(per_job_in_backup))
            raise ConfigError(f"Configurações por job não podem ficar em [backup]: {names}; mova-as para [[jobs]]")
        unknown_parameters(global_values, config_keys, "[backup]", ("backup",))
        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise ConfigError("Use [[jobs]]")
        overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
        selected = overrides.pop("job", None)
        if set(overrides) - config_keys - job_keys:
            raise ConfigError("Override desconhecido")
        global_values.update({k: v for k, v in overrides.items() if k in config_keys})
        base = path.resolve().parent
        environment = dict(os.environ)
        secrets_file = global_values.get("secrets_file")
        if secrets_file is not None and secrets_file != "":
            secrets_file = local_path(secrets_file, base, environment)
            environment = read_environment(secrets_file, environment)
        else:
            secrets_file = None
        global_values["secrets_file"] = secrets_file
        # Only a configured secrets file needs a private subprocess environment.
        global_values["environment"] = environment if secrets_file is not None else None
        for name in ("state_dir", "log_dir"):
            global_values[name] = local_path(global_values.get(name, "state" if name == "state_dir" else "logs"), base, environment)
        for name in ("metadata_dir", "temp_dir"):
            value = global_values.get(name)
            global_values[name] = (local_path(value, base, environment)
                                   if value is not None and value != "" else None)
        if global_values.get("rclone_config"):
            global_values["rclone_config"] = str(local_path(global_values["rclone_config"], base, environment))
        global_values["cronitor_url"] = expand(
            global_values.get("cronitor_url", environment.get("CRONITOR_URL", "")), environment)
        jobs = []
        for index, raw in enumerate(raw_jobs):
            raw = dict(raw)
            job_name = raw.get("name", f"#{index + 1} (sem nome)")
            unknown_parameters(raw, job_keys, f"[[jobs]] (job {job_name!r})", ("jobs", index))
            if selected and raw.get("name") != selected:
                continue
            raw.update({k: v for k, v in overrides.items() if k in job_keys})
            raw["source"] = local_path(raw["source"], base, environment)
            exclude_file = raw.get("exclude_files")
            raw["exclude_files"] = local_path(exclude_file, base, environment) if exclude_file is not None and exclude_file != "" else None
            for name in ("source_mount", "destination_mount"):
                value = raw.get(name)
                raw[name] = local_path(value, base, environment) if value is not None and value != "" else None
            if raw.get("type", "local") == "local":
                raw["destination"] = str(local_path(raw["destination"], base, environment))
            else:
                raw["destination"] = raw["destination"].rstrip("/")
            jobs.append(Job(**raw))
        if any(k in overrides for k in {"source", "destination", "type", "logic"}) and len(jobs) != 1:
            raise ConfigError("Overrides de origem/destino/tipo/lógica exigem um único job (--job)")
        if "max_delete" in overrides and len(jobs) != 1:
            raise ConfigError("--max-delete exige --job para configurar um único job")
        if "max_delete" in overrides and jobs[0].logic != "simple":
            raise ConfigError("--max-delete só se aplica a jobs logic=simple")
        return validate(Config(jobs=tuple(jobs), **global_values))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"Configuração inválida: {exc}") from exc
