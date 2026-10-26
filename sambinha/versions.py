"""Minimum versions of the external programs, checked before they are used.

Older programs do not fail cleanly: rclone rejects flags in the middle of a
run, and 7-Zip before 22.00 silently lists modification times without the
fraction of a second, so archives are later reported as divergent. A version
that cannot be read (custom builds, unexpected banners) is not an error.
"""
import re

from .errors import BackupError

MIN_RCLONE = (1, 60)
# 22.00 is the first 7-Zip whose `l -slt` output keeps sub-second timestamps;
# 21.07 and p7zip 16.02/17.x print whole seconds only.
MIN_7ZIP = (22, 0)

_RCLONE = re.compile(r"\brclone\s+v?(\d+)\.(\d+)")
_7ZIP = re.compile(r"7-Zip\s*(?:\[\d+\]|\([A-Za-z]\))?\s*(\d+)\.(\d+)")


def _parse(pattern, output):
    match = pattern.search(output) if isinstance(output, str) else None
    return (int(match[1]), int(match[2])) if match else None


def parse_rclone_version(output):
    return _parse(_RCLONE, output)


def parse_7zip_version(output):
    return _parse(_7ZIP, output)


def _dotted(version, width=0):
    return f"{version[0]}.{version[1]:0{width}d}"


def check_rclone_version(runner, program):
    """Run `rclone version`; fail when it is older than the supported minimum."""
    version = parse_rclone_version(runner.run([program, "version"]))
    if version is not None and version < MIN_RCLONE:
        raise BackupError(f"rclone {_dotted(version)} é anterior à versão mínima suportada "
                          f"({_dotted(MIN_RCLONE)}); atualize o rclone: {program}")
    return version


def check_compressor_version(runner, program):
    """Read the 7-Zip banner; fail when it is older than the supported minimum."""
    try:
        version = parse_7zip_version(runner.run([program, "-h"]))
    except BackupError:
        # A missing/broken compressor is reported by the operation that needs it.
        return None
    if version is not None and version < MIN_7ZIP:
        raise BackupError(f"7-Zip {_dotted(version, 2)} é anterior à versão mínima suportada "
                          f"({_dotted(MIN_7ZIP, 2)}): versões antigas gravam datas sem fração de segundo "
                          f"e os lotes não passam na conferência; atualize o compressor: {program}")
    return version
