"""Validated TOML configuration and command-line overrides."""
import os
import tomllib
from dataclasses import KW_ONLY, dataclass, field, replace
from pathlib import Path

from .config_locations import key_lines
from .config_validation import validate
from .errors import ConfigError
from .extensions import DEFAULT_COMPRESSED_EXTENSIONS, DEFAULT_TEXT_EXTENSIONS, normalize_extensions
from .names import Logic, StorageType, VerificationMode
from .secrets import expand, read_environment

JOB_SCOPED_OPTIONS = frozenset({"recovery_index_versions", "deleted_days", "version_days", "log_retention_days",
                                "log_file_level", "log_terminal_level", "deletion_alert_threshold",
                                "verify_interval_days", "verification_mode", "fingerprint_cache",
                                "fingerprint_full_scan_days", "verify_source_changes",
                                "transfer_workers", "batch_workers", "dry_run"})
JOB_ONLY_OPTIONS = JOB_SCOPED_OPTIONS | {"control_storage_class"}
EXTENSION_OPTIONS = ("compression_text_extensions", "compression_compressed_extensions")
# Job fields set per invocation by the CLI, never read from TOML.
RUN_SCOPED_FIELDS = frozenset({"adopt_identity", "prune_orphans", "fresh_start"})


@dataclass(frozen=True, slots=True)
class Job:
    """One backup job. ``name``/``source``/``destination`` stay positional; every other field is keyword-only."""

    name: str
    source: Path
    destination: str
    _: KW_ONLY
    type: StorageType = "local"
    logic: Logic = "simple"
    storage_class: str = "GLACIER_IR"
    control_storage_class: str = "STANDARD"
    # Optional rclone tuning, S3 only (never passed for local destinations; see rclone.py).
    fast_list: bool = False  # --fast-list for simple sync/check/lsjson
    s3_chunk_size: str | None = None  # e.g. "64M" -> --s3-chunk-size on uploads
    s3_upload_concurrency: int | None = None  # 1-64 -> --s3-upload-concurrency on uploads
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
    # Three tiers by file extension (see profiles.py): text, already compressed, everything else.
    compression_level: int = 1  # level of the "other" tier (extensions in neither list)
    compression_text_level: int = 1
    compression_compressed_level: int = 0
    # Normalized (lower-case, no dots) tuples; "<none>" stands for files without an extension.
    compression_text_extensions: tuple[str, ...] = DEFAULT_TEXT_EXTENSIONS
    compression_compressed_extensions: tuple[str, ...] = DEFAULT_COMPRESSED_EXTENSIONS
    compression_threads: int = 2
    recovery_index_versions: int = 3
    deleted_days: int = 10
    version_days: int = 2
    log_retention_days: int = 14
    log_file_level: str = "summary"
    log_terminal_level: str = "summary"
    deletion_alert_threshold: int = 1000
    verify_interval_days: int = 0
    verification_mode: VerificationMode = "simple"
    fingerprint_cache: bool = True
    fingerprint_full_scan_days: int = 7
    verify_source_changes: bool = True
    transfer_workers: int = 4
    batch_workers: int = 2
    dry_run: bool = False
    # "auto" resumes from journals/caches; "fresh" discards them each run (see fresh.py).
    resume: str = "auto"
    # Run-scoped only (--adopt-identity): accept state/index of a renamed job or
    # of a legacy identity with other paths. Deliberately not a TOML key.
    adopt_identity: bool = False
    # Run-scoped only (--prune-orphans): delete unreferenced destination objects older than a day.
    prune_orphans: bool = False
    # Run-scoped only (--fresh-start): same as resume = "fresh" for this invocation.
    fresh_start: bool = False

    @property
    def max_bytes(self) -> int:
        """Convert the configured MB (1 MB = 1,048,576 bytes) for packing."""
        return int(self.max_megabytes * 1024 ** 2)


@dataclass(frozen=True, slots=True, kw_only=True)
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


def with_job_overrides(config: Config, **values) -> Config:
    """Copy of ``config`` whose jobs carry the given per-invocation overrides.

    Only ``JOB_SCOPED_OPTIONS`` are accepted (anything else is a ``TypeError``); ``None`` means "no
    override" and is skipped, as the removed ``Config`` override fields did. Validation is unchanged:
    call ``validate`` on the result.
    """
    unknown = set(values) - JOB_SCOPED_OPTIONS
    if unknown:
        raise TypeError(f"Override por job desconhecido: {', '.join(sorted(unknown))}")
    values = {name: value for name, value in values.items() if value is not None}
    if not values:
        return config
    return replace(config, jobs=tuple(replace(job, **values) for job in config.jobs))


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
        job_keys = set(Job.__dataclass_fields__) - RUN_SCOPED_FIELDS
        config_keys = set(Config.__dataclass_fields__) - {"jobs", "environment"}
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
            for name in EXTENSION_OPTIONS:
                if name in raw:
                    raw[name] = normalize_extensions(raw[name], name)
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
