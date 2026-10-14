"""Validated TOML configuration and command-line overrides."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib

from .config_validation import LOG_LEVELS as LOG_LEVELS, STORAGE_CLASSES as STORAGE_CLASSES
from .config_validation import validate as validate
from .errors import ConfigError
from .paths import destinations_overlap as destinations_overlap, overlap as overlap
from .secrets import expand, read_environment


@dataclass(frozen=True)
class Job:
    name: str
    source: Path
    destination: str
    type: str = "local"
    logic: str = "simple"
    storage_class: str = "GLACIER_IR"
    max_bytes: int = 1024 ** 3
    max_files: int = 1000
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
    compression_text_level: int = 3
    compression_text_threads: int = 2
    compression_video_level: int = 0
    compression_video_threads: int = 1
    compression_photo_level: int = 1
    compression_photo_threads: int = 1
    compression_other_level: int = 3
    compression_other_threads: int = 2


@dataclass(frozen=True)
class Config:
    jobs: tuple[Job, ...]
    state_dir: Path = field(default_factory=lambda: Path("state").resolve())
    log_dir: Path = field(default_factory=lambda: Path("logs").resolve())
    log_retention_days: int = 14
    deleted_days: int = 10
    version_days: int = 2
    dry_run: bool = False
    cronitor_url: str = field(default="", repr=False)
    rclone: str = "rclone"
    rclone_config: str = ""
    timeout: int = 3600
    secrets_file: Path | None = None
    environment: dict[str, str] | None = field(default=None, repr=False, compare=False)
    deletion_alert_threshold: int = 1000
    verify_interval_days: int = 0
    fingerprint_cache: bool = True
    verification_mode: str = "simple"
    fingerprint_full_scan_days: int = 7
    transfer_workers: int = 4
    batch_workers: int = 2
    log_file_level: str = "summary"
    log_terminal_level: str = "summary"


def local_path(value, base, environment=None):
    if not isinstance(value, str) or not value:
        raise ConfigError("Caminho deve ser uma string não vazia")
    path = Path(expand(value, os.environ if environment is None else environment)).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load(path: Path, overrides: dict | None = None) -> Config:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        if set(data) - {"backup", "jobs"}:
            raise ConfigError("Seções desconhecidas no TOML")
        global_values = dict(data.get("backup", {}))
        job_keys = set(Job.__dataclass_fields__)
        config_keys = set(Config.__dataclass_fields__) - {"jobs", "environment"}
        if set(global_values) - config_keys:
            raise ConfigError("Parâmetros desconhecidos em [backup]")
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
        if global_values.get("rclone_config"):
            global_values["rclone_config"] = str(local_path(global_values["rclone_config"], base, environment))
        global_values["cronitor_url"] = expand(
            global_values.get("cronitor_url", environment.get("CRONITOR_URL", "")), environment)
        jobs = []
        for raw in raw_jobs:
            raw = dict(raw)
            if set(raw) - job_keys:
                raise ConfigError("Parâmetros desconhecidos em [[jobs]]")
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
