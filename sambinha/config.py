"""Validated TOML configuration and command-line overrides."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from .errors import ConfigError
from .secrets import expand, read_environment

STORAGE_CLASSES = {"STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING",
                   "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"}


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


def local_path(value, base, environment=None):
    if not isinstance(value, str) or not value:
        raise ConfigError("Caminho deve ser uma string não vazia")
    path = Path(expand(value, os.environ if environment is None else environment)).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def destinations_overlap(first: Job, second: Job) -> bool:
    if first.type != second.type:
        return False
    if first.type == "local":
        return overlap(Path(first.destination).resolve(), Path(second.destination).resolve())
    def prefix(value):
        remote, path = value.split(":", 1)
        parts = tuple(part for part in path.split("/") if part)
        return remote, parts
    first_remote, first_parts = prefix(first.destination)
    second_remote, second_parts = prefix(second.destination)
    return (first_remote == second_remote and
            (first_parts[:len(second_parts)] == second_parts or
             second_parts[:len(first_parts)] == first_parts))


def validate(config: Config) -> Config:
    if not config.jobs:
        raise ConfigError("Configure pelo menos um [[jobs]]")
    for name in ("deleted_days", "version_days", "timeout", "deletion_alert_threshold", "verify_interval_days",
                 "log_retention_days"):
        value = getattr(config, name)
        if type(value) is not int or value < (1 if name == "timeout" else 0):
            raise ConfigError(f"{name}: inteiro fora do intervalo permitido")
    if type(config.dry_run) is not bool:
        raise ConfigError("dry_run deve ser booleano")
    if type(config.fingerprint_cache) is not bool:
        raise ConfigError("fingerprint_cache deve ser booleano")
    if not isinstance(config.verification_mode, str) or config.verification_mode not in {"simple", "hash"}:
        raise ConfigError("verification_mode deve ser simple ou hash")
    for name in ("fingerprint_full_scan_days", "transfer_workers", "batch_workers"):
        if type(getattr(config, name)) is not int or getattr(config, name) < 1:
            raise ConfigError(f"{name} deve ser um inteiro positivo")
    if not isinstance(config.cronitor_url, str) or (config.cronitor_url and
            urlsplit(config.cronitor_url).scheme != "https"):
        raise ConfigError("cronitor_url deve usar HTTPS")
    if not isinstance(config.rclone, str) or not config.rclone:
        raise ConfigError("rclone deve indicar um executável")
    if not isinstance(config.rclone_config, str):
        raise ConfigError("rclone_config deve ser uma string")
    if config.secrets_file is not None and not isinstance(config.secrets_file, Path):
        raise ConfigError("secrets_file deve indicar um caminho")
    seen = set()
    for job in config.jobs:
        if not isinstance(job.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job.name):
            raise ConfigError("Nome de job inválido; use letras, números, _ e -")
        if job.name in seen:
            raise ConfigError(f"Job duplicado: {job.name}")
        seen.add(job.name)
        for name in ("check_source_disk", "check_destination_disk", "continue_on_job_error"):
            if type(getattr(job, name)) is not bool:
                raise ConfigError(f"{name} deve ser booleano: {job.name}")
        if job.type not in {"local", "s3"} or job.logic not in {"simple", "batch"}:
            raise ConfigError(f"Tipo ou lógica inválidos: {job.name}")
        if job.storage_class not in STORAGE_CLASSES:
            raise ConfigError(f"Classe S3 inválida: {job.storage_class}")
        if job.type == "s3" and job.logic == "simple" and job.storage_class in {"GLACIER", "DEEP_ARCHIVE"}:
            raise ConfigError("simple exige S3 acessível sem restore; use GLACIER_IR ou batch")
        if type(job.max_bytes) is not int or job.max_bytes < 1:
            raise ConfigError("max_bytes deve ser positivo")
        if type(job.max_files) is not int or job.max_files < 2:
            raise ConfigError("max_files deve ser pelo menos 2")
        if not isinstance(job.compressor, str) or not job.compressor:
            raise ConfigError("compressor deve indicar um executável compatível com 7-Zip")
        if job.archive_format not in {"7z", "zip"}:
            raise ConfigError("archive_format deve ser 7z ou zip")
        if job.logic == "simple":
            if type(job.max_delete) is not int or job.max_delete < 0:
                raise ConfigError(f"max_delete deve ser um inteiro não negativo por job: {job.name}")
        else:
            if type(job.max_delete_percent) not in (int, float) or not 0 <= job.max_delete_percent <= 100:
                raise ConfigError("max_delete_percent deve estar entre 0 e 100")
            if type(job.deletion_min_files) is not int or job.deletion_min_files < 1:
                raise ConfigError("deletion_min_files deve ser pelo menos 1")
        for kind in ("text", "video", "photo", "other"):
            level, threads = getattr(job, f"compression_{kind}_level"), getattr(job, f"compression_{kind}_threads")
            if type(level) is not int or not 0 <= level <= 9:
                raise ConfigError(f"compression_{kind}_level deve estar entre 0 e 9")
            if type(threads) is not int or not 1 <= threads <= 64:
                raise ConfigError(f"compression_{kind}_threads deve estar entre 1 e 64")
        if job.exclude_files is not None and not isinstance(job.exclude_files, Path):
            raise ConfigError("exclude_files deve indicar um caminho")
        for name in ("source_mount", "destination_mount"):
            if getattr(job, name) is not None and not isinstance(getattr(job, name), Path):
                raise ConfigError(f"{name} deve indicar um caminho")
        if job.type == "s3" and job.destination_mount is not None:
            raise ConfigError("destination_mount é aplicável somente a destinos locais")
        if not isinstance(job.destination, str) or not job.destination:
            raise ConfigError("Destino vazio")
        for internal in (config.state_dir, config.log_dir):
            if overlap(job.source, internal):
                raise ConfigError("Origem não pode sobrepor diretórios de estado ou logs")
        if job.type == "local":
            target = Path(job.destination).resolve()
            if overlap(job.source, target):
                raise ConfigError(f"Destino local não pode sobrepor a própria origem: {job.name}")
            if any(overlap(target, p) for p in (config.state_dir, config.log_dir)):
                raise ConfigError("Destino não pode sobrepor estado ou logs")
        elif not re.fullmatch(r"[\w-]+:[^/][^\n\r]*", job.destination):
            raise ConfigError("S3: use remote:bucket/prefixo configurado no rclone")
    for index, job in enumerate(config.jobs):
        for other in config.jobs[index + 1:]:
            if destinations_overlap(job, other):
                raise ConfigError(f"Destinos dos jobs {job.name} e {other.name} se sobrepõem")
    if overlap(config.state_dir, config.log_dir):
        raise ConfigError("Estado e logs devem ter diretórios separados")
    return config


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
