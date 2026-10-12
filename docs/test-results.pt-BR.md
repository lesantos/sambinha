# Resultados dos testes do Sambinha 0.8.0

[English](test-results.md)

Executado em 2026-10-12, Linux, Python 3.12.3: **287 testes passaram, sem falhas nem testes ignorados**, em **776.643 segundos**. Cobertura incluindo ramos: **94%**, acima do mínimo de 85%.

## Comportamento verificado

Verificadas as opções de disco na origem/destino e na saída da restauração, mantendo proteções de temporários, montagens e caminhos. Políticas diferentes por job foram testadas no preflight, inventário, exclusões, estado inválido, transferências e verificação manual/agendada; as mensagens identificam o job que falhou.

Os testes de links cobrem arquivos, diretórios, links quebrados, ciclos, variáveis do rclone para seguir links, colisões na origem/destino, proteção de cópias/lotes/históricos expirados e redirecionamento após o inventário. READMEs bilíngues resumidos, guias completos, links recíprocos e equivalência dos exemplos foram conferidos.

Todos os testes anteriores de backup, retenção, compactação, filtros, concorrência, trava de escrita, restauração, verificação, ocultação de segredos e progresso foram repetidos. Usam dados temporários, rclone/7-Zip reais e Moto em processo e por HTTP local, sem serviços reais AWS ou Cronitor.

## Distribuição

| Suíte | Aprovados |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 16 |
| `tests.test_disks` | 10 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_job_errors` | 10 |
| `tests.test_local_integration` | 28 |
| `tests.test_log_retention` | 9 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_progress` | 13 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 15 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 21 |
| `tests.test_s3_integration` | 19 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |
| `tests.test_symlinks` | 7 |
| `tests.test_verification_modes` | 13 |
| **TOTAL** | **287** |

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
sambinha/config.py         210      4    116      4    98%   144, 147, 192, 215
sambinha/disks.py           26      0     14      0   100%
sambinha/engine.py         549     12    198     20    96%   106, 134->124, 155->157, 167, 176->178, 182->184, 197->173, 203->205, 265->267, 285->287, 310->308, 313, 316, 469, 485-486, 490, 492->509, 502, 508, 620, 629
sambinha/errors.py          18      0      4      0   100%
sambinha/filtering.py       93      6     34      2    92%   41-45, 74->76, 84
sambinha/inventory.py      140      6     72      5    95%   39, 46, 54, 65, 101, 120
sambinha/logging.py         79      1     26      2    97%   63->68, 71
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         71      2     18      1    97%   52-53, 69->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/progress.py        88      0     14      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       246     15    116     13    92%   43, 76, 82, 89, 91, 94, 102, 124, 150->148, 168, 207, 224, 250, 275-277
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          119     16     70     17    83%   25, 46, 52, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 117, 120, 123, 125, 131
sambinha/storage.py        103     12     38      3    88%   75-76, 81-82, 83->85, 107, 109-110, 128-129, 132-134
--------------------------------------------------------------------
TOTAL                     1982     84    804     71    94%
```
