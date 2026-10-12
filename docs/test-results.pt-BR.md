# Resultados dos testes do Sambinha 0.7.0

[English](test-results.md)

Execução em 2026-10-11, Linux, Python 3.12.3: **260 testes passaram, sem falhas ou testes ignorados**, em **497.287 segundos**. Cobertura com branches: **94%**, acima do mínimo de 85%.

## Comportamento verificado

- Retenção configurável de 14 dias de logs, overrides TOML/CLI, zero desativando a limpeza, limite exato de idade e proteção de logs atuais/abertos, arquivos de outros tipos, links e subdiretórios. Testes cobrem corridas de substituição/remoção e limpeza em backup/restore/verify; simulações preservam logs antigos.
- Percentual no começo da linha, crescente entre diretórios, etapas, tarefas e lotes concorrentes, com 100% somente após sucesso. Grupos de cópia mostram o subdiretório real. Detalhes por arquivo permanecem na auditoria JSON e são ocultados do terminal interativo.
- Inventário por metadados como padrão, comparando tamanho/data em nanossegundos; modo hash, cache/leitura completa, troca de modos, cópia local e HTTP/S3, integridade e restauração continuam cobertos.
- Todos os testes anteriores de planejamento, histórico, retenção, proteções de disco/montagem/remoção, trava de execução, destino mais novo, filtros, segredos, notificações, concorrência/perfis e exemplos públicos bilíngues foram repetidos.

Os testes usam dados temporários, rclone e 7-Zip reais e Moto em processo e via HTTP local. Nenhum serviço real AWS/Cronitor foi usado. A versão e a CLI/retenção padrão também passaram usando o wheel construído em uma pasta isolada.

## Distribuição

| Suíte | Passaram |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 13 |
| `tests.test_disks` | 5 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_local_integration` | 28 |
| `tests.test_log_retention` | 9 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_progress` | 13 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 13 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 21 |
| `tests.test_s3_integration` | 19 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |
| `tests.test_verification_modes` | 13 |

## Reproduzir

```bash
.venv/bin/python -m pip install -e . -r requirements-test.txt
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
sambinha/__init__.py         1      0      0      0   100%
sambinha/batches.py         56      0     26      0   100%
sambinha/cli.py             47      6      8      1    84%   52-54, 58-60
sambinha/config.py         204      4    112      4    97%   138, 141, 186, 209
sambinha/disks.py           22      1     12      1    94%   15
sambinha/engine.py         531     14    190     20    95%   101, 129->119, 148->150, 160, 169->171, 175->177, 190->166, 196->198, 203-204, 254->256, 272->274, 297->295, 300, 303, 457, 469-470, 474, 480->482, 493, 499, 599, 608
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       88      8     34      3    89%   22-23, 38-42, 70->72, 80
sambinha/inventory.py      128      6     66      5    94%   37, 44, 52, 61, 84, 105
sambinha/logging.py         79      1     26      2    97%   63->68, 71
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         71      2     18      1    97%   52-53, 69->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/progress.py        88      0     14      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       239     17    108     15    91%   36, 43, 76, 82, 89, 91, 94, 102, 124, 150->148, 168, 206, 222, 248, 267-270
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          119     16     70     17    83%   25, 46, 52, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 117, 120, 123, 125, 131
sambinha/storage.py         96     12     34      3    87%   70-71, 76-77, 78->80, 102, 104-105, 121-122, 125-127
--------------------------------------------------------------------
TOTAL                     1907     91    768     75    94%
```
