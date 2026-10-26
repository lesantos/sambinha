"""CLI: `sambinha [backup] [options]` or `sambinha <action> [options]`; omitted options inherit TOML values."""
import argparse
import sys
from pathlib import Path

from . import __version__
from .cli_options import OVERRIDES
from .config import load
from .engine import run
from .errors import BackupError
from .notify import notify
from .process import Terminated
from .recovery import recover

ACTIONS = ("backup", "restore", "rebalance", "verify-local-metadata", "verify-destination-metadata",
           "verify-destination-integrity")


class _Parser(argparse.ArgumentParser):
    """Rejects the pre-0.17 order `sambinha --job x restore` with a hint instead of dropping options silently."""

    def parse_known_args(self, args=None, namespace=None):
        tokens = list(sys.argv[1:] if args is None else args)
        # Only the root parser (the one owning subparsers) sees the action word after options.
        if self.prog == "sambinha":
            for index, token in enumerate(tokens):
                if token == "--":
                    break
                if token.split("=")[0] in ("--output", "--path") and not any(t in ACTIONS for t in tokens[:index]):
                    self.error(f"{token.split('=')[0]} só existe em 'sambinha restore'")
                if token in ACTIONS and index > 0 and not self._is_option_value(tokens, index):
                    rest = tokens[:index] + tokens[index + 1:]
                    self.error(f"a ação '{token}' deve vir antes das opções; use: sambinha {token} {' '.join(rest)}")
        return super().parse_known_args(tokens, namespace)

    def _is_option_value(self, tokens, index):
        previous = tokens[index - 1]
        action = self._option_string_actions.get(previous)
        return action is not None and action.nargs != 0


def _add_override(group, name, option):
    flag = "--" + name.replace("_", "-")
    if option.type is bool:
        group.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, help=option.help)
    elif isinstance(option.type, tuple):
        group.add_argument(flag, choices=option.type, default=None, help=option.help)
    else:
        group.add_argument(flag, type=option.type, default=None, help=option.help)


def _common():
    """Options shared by every action: config file plus all table overrides."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("sambinha.toml"), help="Arquivo TOML de configuração")
    for name, option in OVERRIDES.items():
        _add_override(common, name, option)
    return common


def _backup_options():
    backup = argparse.ArgumentParser(add_help=False)
    backup.add_argument("--allow-mass-deletion", action="store_true", help="Autorizar remoções em massa nesta execução")
    backup.add_argument("--adopt-identity", action="store_true",
                        help="Assumir um backup existente de outro nome de job ou identidade antiga com caminhos diferentes")
    backup.add_argument("--prune-orphans", action="store_true",
                        help="Remover do destino lotes/gerações órfãos com mais de 1 dia")
    backup.add_argument("--fresh-start", action="store_true",
                        help="Descartar caches e journals de retomada (mantém o índice confirmado)")
    return backup


def parser():
    common, backup = _common(), _backup_options()
    result = _Parser(prog="sambinha", parents=[common, backup],
                     description="Backup local/S3 simples ou por lotes. Sem ação, executa backup.",
                     epilog="As opções vêm depois da ação: sambinha restore --job documentos --output PASTA")
    result.add_argument("--version", action="version", version=__version__)
    actions = result.add_subparsers(dest="action", metavar="ação", parser_class=argparse.ArgumentParser)
    result.set_defaults(action="backup")  # after add_subparsers, which would otherwise default to None
    actions.add_parser("backup", parents=[common, backup], help="Executar o backup (padrão)")
    # rebalance is a run mode of the batch engine and honours the mass-deletion guard like backup.
    rebalance = argparse.ArgumentParser(add_help=False)
    rebalance.add_argument("--allow-mass-deletion", action="store_true", help="Autorizar remoções em massa nesta execução")
    actions.add_parser("rebalance", parents=[common, rebalance], help="Reorganizar lotes pelos limites atuais")
    restore = actions.add_parser("restore", parents=[common], help="Restaurar arquivos de um job")
    restore.add_argument("--output", type=Path, help="Pasta nova ou vazia para restauração")
    restore.add_argument("--path", dest="paths", action="append", default=[], help="Arquivo ou pasta do índice; repetível")
    for name, text in (("verify-local-metadata", "Comparar a origem com os controles locais (diário)"),
                       ("verify-destination-metadata", "Comparar controles locais e do destino (mensal)"),
                       ("verify-destination-integrity", "Testar a integridade dos lotes no destino (manual)")):
        actions.add_parser(name, parents=[common], help=text)
    return result


def main(argv=None):
    arguments = vars(parser().parse_args(argv))
    path = arguments.pop("config")
    action = arguments.pop("action")
    output = arguments.pop("output", None)
    selectors = arguments.pop("paths", [])
    allowed = arguments.pop("allow_mass_deletion", False)
    adopted = arguments.pop("adopt_identity", False)
    prune = arguments.pop("prune_orphans", False)
    fresh = arguments.pop("fresh_start", False)
    try:
        config = load(path, {name: arguments.get(name) for name in OVERRIDES})
        if action not in {"backup", "rebalance"}:
            return recover(config, action, output=output, selectors=selectors, notifier=notify)
        return run(config, allow_mass_deletion=allowed, action=action, adopt_identity=adopted,
                   prune_orphans=prune, fresh_start=fresh)
    except (BackupError, OSError) as exc:
        print(f"sambinha: {exc}", file=sys.stderr)
        return 2
    except Terminated:
        # 128 + SIGTERM(15), the shell convention for a process ended by SIGTERM.
        print("sambinha: execução encerrada por SIGTERM", file=sys.stderr)
        return 143
    except KeyboardInterrupt:
        print("sambinha: execução interrompida", file=sys.stderr)
        return 130
