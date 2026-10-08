"""CLI with explicit overrides; omitted arguments inherit TOML values."""
import argparse
from pathlib import Path
import sys

from . import __version__
from .config import load
from .engine import run
from .errors import BackupError


def parser():
    result = argparse.ArgumentParser(prog="smabafiba", description="Backup local/S3 simples ou por lotes")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", type=Path, default=Path("smabafiba.toml"))
    result.add_argument("--job", help="Executar apenas o job nomeado")
    result.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    for name in ("source", "destination", "type", "logic", "storage_class", "compressor", "archive_format",
                 "state_dir", "log_dir", "cronitor_url", "rclone", "rclone_config"):
        result.add_argument("--" + name.replace("_", "-"), default=None)
    for name in ("deleted_days", "version_days", "max_bytes", "max_files", "timeout"):
        result.add_argument("--" + name.replace("_", "-"), type=int, default=None)
    return result


def main(argv=None):
    arguments = vars(parser().parse_args(argv))
    path = arguments.pop("config")
    try:
        return run(load(path, arguments))
    except (BackupError, OSError) as exc:
        print(f"smabafiba: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("smabafiba: execução interrompida", file=sys.stderr)
        return 130
