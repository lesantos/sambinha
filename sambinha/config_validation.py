"""Ordered validators for global settings, jobs and destination boundaries."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .errors import ConfigError
from .extensions import check_extension_lists, normalize_extensions
from .paths import destinations_overlap, overlap

if TYPE_CHECKING:
    from .config import Config, Job


STORAGE_CLASSES = {"STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING",
                   "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"}
LOG_LEVELS = {"error", "summary", "detailed"}
# rclone size with an optional binary suffix: 16M, 64Mi, 1G (S3 chunks are at least 5 MiB).
S3_SIZE = re.compile(r"[1-9][0-9]{0,5}[KMG]i?")


def _validate_global(config: Config):
    if not config.jobs:
        raise ConfigError("Configure pelo menos um [[jobs]]")
    if type(config.timeout) is not int or config.timeout < 1:
        raise ConfigError("timeout: inteiro fora do intervalo permitido")
    if not isinstance(config.cronitor_url, str) or (config.cronitor_url and
            urlsplit(config.cronitor_url).scheme != "https"):
        raise ConfigError("cronitor_url deve usar HTTPS")
    if not isinstance(config.rclone, str) or not config.rclone:
        raise ConfigError("rclone deve indicar um executável")
    if not isinstance(config.rclone_config, str):
        raise ConfigError("rclone_config deve ser uma string")
    if config.secrets_file is not None and not isinstance(config.secrets_file, Path):
        raise ConfigError("secrets_file deve indicar um caminho")
    if config.metadata_dir is not None and not isinstance(config.metadata_dir, Path):
        raise ConfigError("metadata_dir deve indicar um caminho")
    if config.temp_dir is not None and not isinstance(config.temp_dir, Path):
        raise ConfigError("temp_dir deve indicar um caminho")


def _validate_job(job: Job, seen: set[str]):
    if not isinstance(job.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job.name):
        raise ConfigError("Nome de job inválido; use letras, números, _ e -")
    if job.name in seen:
        raise ConfigError(f"Job duplicado: {job.name}")
    seen.add(job.name)
    for name in ("check_source_disk", "check_destination_disk", "continue_on_job_error", "dry_run",
                 "fingerprint_cache", "verify_source_changes"):
        if type(getattr(job, name)) is not bool:
            raise ConfigError(f"{name} deve ser booleano: {job.name}")
    for name in ("deleted_days", "version_days", "log_retention_days", "deletion_alert_threshold", "verify_interval_days"):
        if type(getattr(job, name)) is not int or getattr(job, name) < 0:
            raise ConfigError(f"{name}: inteiro não negativo por job: {job.name}")
    for name in ("fingerprint_full_scan_days", "transfer_workers", "batch_workers", "recovery_index_versions"):
        if type(getattr(job, name)) is not int or getattr(job, name) < 1:
            raise ConfigError(f"{name} deve ser um inteiro positivo por job: {job.name}")
    for name in ("log_file_level", "log_terminal_level"):
        if not isinstance(getattr(job, name), str) or getattr(job, name) not in LOG_LEVELS:
            raise ConfigError(f"{name} deve ser error, summary ou detailed: {job.name}")
    if not isinstance(job.verification_mode, str) or job.verification_mode not in {"simple", "hash"}:
        raise ConfigError(f"verification_mode deve ser simple ou hash: {job.name}")
    if not isinstance(job.resume, str) or job.resume not in {"auto", "fresh"}:
        raise ConfigError(f"resume deve ser auto ou fresh: {job.name}")
    if job.type not in {"local", "s3"} or job.logic not in {"simple", "batch"}:
        raise ConfigError(f"Tipo ou lógica inválidos: {job.name}")
    if job.storage_class not in STORAGE_CLASSES:
        raise ConfigError(f"Classe S3 inválida: {job.storage_class}")
    if job.control_storage_class not in STORAGE_CLASSES:
        raise ConfigError(f"Classe S3 de controles inválida: {job.control_storage_class}")
    if job.type == "s3" and job.control_storage_class in {"GLACIER", "DEEP_ARCHIVE"}:
        raise ConfigError("control_storage_class deve permitir leitura imediata; use STANDARD, STANDARD_IA, ONEZONE_IA, INTELLIGENT_TIERING ou GLACIER_IR")
    if job.type == "s3" and job.logic == "simple" and job.storage_class in {"GLACIER", "DEEP_ARCHIVE"}:
        raise ConfigError("simple exige S3 acessível sem restore; use GLACIER_IR ou batch")
    value = job.max_megabytes
    if (type(value) not in (int, float) or value <= 0
            or (isinstance(value, float) and (not math.isfinite(value)
                                            or not math.isfinite(value * 1024 ** 2)))
            or job.max_bytes < 1):
        raise ConfigError("max_megabytes deve ser um número positivo e finito equivalente a pelo menos 1 byte")
    for name, minimum, maximum in (("individual_batch_percent", 0, 100),
                                   ("batch_size_alert_percent", -1, float('inf'))):
        value = getattr(job, name)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum < value <= maximum or (name == "batch_size_alert_percent" and value < 0):
            raise ConfigError(f"{name}: percentual fora do intervalo permitido")
    if not isinstance(job.compressor, str) or not job.compressor:
        raise ConfigError("compressor deve indicar um executável compatível com 7-Zip")
    if type(job.fast_list) is not bool:
        raise ConfigError(f"fast_list deve ser true ou false: {job.name}")
    if job.s3_chunk_size is not None and not (isinstance(job.s3_chunk_size, str)
                                              and S3_SIZE.fullmatch(job.s3_chunk_size)):
        raise ConfigError(f"s3_chunk_size deve ser um tamanho como 16M, 64M ou 1G: {job.name}")
    if job.s3_upload_concurrency is not None and (type(job.s3_upload_concurrency) is not int
                                                  or not 1 <= job.s3_upload_concurrency <= 64):
        raise ConfigError(f"s3_upload_concurrency deve ser um inteiro entre 1 e 64: {job.name}")
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
    for name in ("compression_level", "compression_text_level", "compression_compressed_level"):
        if type(getattr(job, name)) is not int or not 0 <= getattr(job, name) <= 9:
            raise ConfigError(f"{name} deve ser um inteiro entre 0 e 9")
    for name in ("compression_text_extensions", "compression_compressed_extensions"):
        value = getattr(job, name)
        # The frozen Job holds the normalized tuple (config.load builds it); a hand-built Job must too.
        if not isinstance(value, tuple) or normalize_extensions(value, name) != value:
            raise ConfigError(f"{name} deve ser uma tupla de extensões normalizadas (minúsculas, sem ponto)")
    check_extension_lists(job.compression_text_extensions, job.compression_compressed_extensions)
    if type(job.compression_threads) is not int or not 1 <= job.compression_threads <= 64:
        raise ConfigError("compression_threads deve estar entre 1 e 64")


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
    internal_paths = (config.state_dir, config.log_dir,
                      *((config.metadata_dir,) if config.metadata_dir else ()),
                      *((config.temp_dir,) if config.temp_dir else ()))
    for internal in internal_paths:
        if overlap(job.source, internal):
            raise ConfigError("Origem não pode sobrepor estado, logs, metadados ou temporários")
    if job.type == "local":
        target = Path(job.destination).resolve()
        if overlap(job.source, target):
            raise ConfigError(f"Destino local não pode sobrepor a própria origem: {job.name}")
        if any(overlap(target, p) for p in internal_paths):
            raise ConfigError("Destino não pode sobrepor estado, logs, metadados ou temporários")
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
    if config.metadata_dir and overlap(config.metadata_dir, config.log_dir):
        raise ConfigError("Metadados e logs devem ter diretórios separados")
    if config.temp_dir and any(overlap(config.temp_dir, path) for path in
                               (config.state_dir, config.log_dir, config.metadata_dir) if path is not None):
        raise ConfigError("Diretório temporário deve ser separado de estado, logs e metadados")
    return config
