"""Strict dotenv input and variable expansion, without running shell code."""
import os
import re
from collections.abc import Mapping
from pathlib import Path

from dotenv.parser import parse_stream

from .errors import ConfigError

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def expand(value: str, environment: Mapping[str, str]) -> str:
    """Expand named variables once; unknown names remain literal, like expandvars."""
    return _VARIABLE.sub(lambda match: environment.get(
        match.group(1) or match.group(2), match.group(0)), value)


def read_environment(path: Path, inherited: Mapping[str, str] | None = None) -> dict[str, str]:
    """Parse every binding before merging; error messages never include values.

    Dotenv handles quotes, comments, optional export and multiline strings.
    Values are kept literal: no shell commands or dotenv variable interpolation.
    The returned mapping belongs to this configuration, not the parent process.
    """
    values: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for binding in parse_stream(stream):
                if binding.error or (binding.key is not None and (
                        not _NAME.fullmatch(binding.key) or binding.value is None
                        or "\x00" in binding.value or binding.key in values)):
                    raise ConfigError(f"secrets_file inválido na linha {binding.original.line}; "
                                      "use uma atribuição NOME=valor por variável")
                if binding.key is not None and binding.value is not None:  # value checked above; narrows for mypy
                    values[binding.key] = binding.value
    except (OSError, UnicodeError):
        raise ConfigError("Não foi possível ler secrets_file; confira o caminho, "
                          "a codificação UTF-8 e a permissão de leitura") from None
    # Existing terminal values, including empty strings, take precedence.
    return {**values, **(os.environ if inherited is None else inherited)}
