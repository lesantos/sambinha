"""Ordered validators for global settings, jobs and destination boundaries."""
from __future__ import annotations
from pathlib import Path
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .errors import ConfigError
from .paths import destinations_overlap, overlap

if TYPE_CHECKING:
    from .config import Config, Job


STORAGE_CLASSES = {"STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING",
                   "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"}
LOG_LEVELS = {"error", "summary", "detailed"}


def _validate_global(config: Config):
    if not config.jobs:
        raise ConfigError("Configure pelo menos um [[jobs]]")
    for name in ("deleted_days", "version_days", "timeout", "deletion_alert_threshold", "verify_interval_days",
                 "log_retention_days"):
        value = getattr(config, name)
        if type(value) is not int or value < (1 if name == "timeout" else 0):
            raise ConfigError(f"{name}: inteiro fora do intervalo permitido")
    if type(config.dry_run) is not bool:
        raise ConfigError("dry_run deve ser booleano")
    for name in ("log_file_level", "log_terminal_level"):
        value = getattr(config, name)
        if not isinstance(value, str) or value not in LOG_LEVELS:
            raise ConfigError(f"{name} deve ser error, summary ou detailed")
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


def _validate_job(job: Job, seen: set[str]):
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


def _validate_job_paths(config: Config, job: Job):
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


def _validate_destinations(config: Config):
    for index, job in enumerate(config.jobs):
        for other in config.jobs[index + 1:]:
            if destinations_overlap(job, other):
                raise ConfigError(f"Destinos dos jobs {job.name} e {other.name} se sobrepõem")


def validate(config: Config) -> Config:
    """Preserve validation order so the first reported error stays stable."""
    _validate_global(config)
    seen = set()
    for job in config.jobs:
        _validate_job(job, seen)
        _validate_job_paths(config, job)
    _validate_destinations(config)
    if overlap(config.state_dir, config.log_dir):
        raise ConfigError("Estado e logs devem ter diretórios separados")
    return config
