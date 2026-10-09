"""Errors exposed to the command-line user without a traceback."""


class BackupError(Exception):
    """An operation could not be completed safely."""


class ConfigError(BackupError):
    """Configuration violates a required constraint."""
