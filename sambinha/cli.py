"""CLI with explicit overrides; omitted arguments inherit TOML values."""
import argparse
from pathlib import Path
import sys

from . import __version__
from .config import load
from .engine import run
from .errors import BackupError
from .notify import notify
from .recovery import recover


def parser():
    result = argparse.ArgumentParser(prog="sambinha", description="Backup local/S3 simples ou por lotes")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("action", nargs="?", choices=("backup", "restore", "verify"), default="backup")
    result.add_argument("--config", type=Path, default=Path("sambinha.toml"))
    result.add_argument("--job", help="Executar apenas o job nomeado")
    result.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--output", type=Path, help="Pasta nova ou vazia para restauração")
    result.add_argument("--path", dest="paths", action="append", default=[], help="Arquivo ou pasta do índice; repetível")
    result.add_argument("--allow-mass-deletion", action="store_true", help="Autorizar remoções em massa nesta execução")
    result.add_argument("--max-delete-percent", type=float, default=None)
    result.add_argument("--max-delete", type=int, default=None,
                        help="Sobrepor max_delete absoluto do job simples; requer --job")
    result.add_argument("--fingerprint-cache", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--verification-mode", choices=("simple", "hash"), default=None,
                        help="Comparar tamanho/data de alteração (simple) ou conteúdo (hash)")
    for name in ("log_file_level", "log_terminal_level"):
        result.add_argument("--" + name.replace("_", "-"), choices=("error", "summary", "detailed"), default=None)
    for name in ("source", "destination", "type", "logic", "storage_class", "compressor", "archive_format",
                 "state_dir", "log_dir", "cronitor_url", "rclone", "rclone_config", "secrets_file", "exclude_files",
                 "source_mount", "destination_mount"):
        result.add_argument("--" + name.replace("_", "-"), default=None)
    for name in ("deleted_days", "version_days", "log_retention_days", "max_bytes", "max_files", "timeout", "deletion_alert_threshold",
                 "deletion_min_files", "verify_interval_days", "fingerprint_full_scan_days",
                 "transfer_workers", "batch_workers",
                 *(f"compression_{kind}_{setting}" for kind in ("text", "video", "photo", "other")
                   for setting in ("level", "threads"))):
        result.add_argument("--" + name.replace("_", "-"), type=int, default=None)
    return result


def main(argv=None):
    arguments = vars(parser().parse_args(argv))
    path = arguments.pop("config")
    action = arguments.pop("action")
    output = arguments.pop("output")
    selectors = arguments.pop("paths")
    allowed = arguments.pop("allow_mass_deletion")
    try:
        config = load(path, arguments)
        if action != "backup":
            return recover(config, action, output=output, selectors=selectors, notifier=notify)
        if output is not None or selectors:
            raise BackupError("--output e --path exigem restore")
        return run(config, allow_mass_deletion=allowed)
    except (BackupError, OSError) as exc:
        print(f"sambinha: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("sambinha: execução interrompida", file=sys.stderr)
        return 130
