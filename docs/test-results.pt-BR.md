# Resultados dos testes do Sambinha 0.6.0

[English](test-results.md)

Execução em 2026-10-09, Linux, Python 3.12.3: **222 testes aprovados, sem falhas ou testes ignorados**, em **287.499 segundos**. Cobertura incluindo decisões: **92%**, acima do mínimo de 85%.

## Comportamento verificado

* Novos lotes por data, do mais antigo ao mais recente, e preservação de lotes intactos/legados; mudanças/remoções só substituem lotes afetados.
* Restauração de arquivos/pastas do índice ativo, recuperação de índice perdido dos lotes, dados binários, permissões comuns, checksums antes da publicação e recusa de sobrescrita/caminhos inseguros/links.
* Verificação local por conteúdo e datas de sucesso/falha da agenda. Verificação S3 só por metadados. O rclone real verificou contra servidor HTTP que permitia HEAD e recusava GET, sem requisitar corpos de objetos.
* Bloqueio de remoções antes de mudanças no destino/estado, limite exato e autorização por uma execução. Ocupação acima de 95% falha antes do backup e depois de uma última transferência que ultrapasse o limite.
* Metadados/logs/lock privados com umask permissivo, recusa de índices corrompidos, conflitos ativo/histórico e ocultação de senhas/URLs/segredos multilinha.
* Cache sem releitura de conteúdo, ctime detectando mudanças de tamanho/mtime iguais, leitura completa periódica, desativação do cache e inventário compartilhado.
* Cópias agrupadas limitadas, workers concorrentes limitados, perfis por categoria e cache de filtros na execução.
* Recuperação local/S3 simples/lotes existente, proteção de destino mais novo, filtros/montagens/retenção/notificações e exemplos bilíngues equivalentes com links recíprocos.

Testes usaram dados temporários, rclone/7-Zip reais e Moto em memória e via HTTP localhost. Nenhum serviço AWS ou Cronitor real foi usado. Testes de performance conferem redução de leituras/chamadas e concorrência; não garantem tempos em produção.

## Distribuição

| Suíte | Aprovados |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 12 |
| `tests.test_disks` | 5 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_local_integration` | 28 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 13 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 20 |
| `tests.test_s3_integration` | 18 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |

## Reproduzir

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
sambinha/__init__.py         1      0      0      0   100%
sambinha/batches.py         56      0     26      0   100%
sambinha/cli.py             46      6      8      1    83%   50-52, 56-58
sambinha/config.py         200      4    110      4    97%   133, 136, 181, 204
sambinha/disks.py           22      1     12      1    94%   15
sambinha/engine.py         432     14    150     12    96%   93, 121->111, 147, 173-174, 238->236, 241, 244, 389, 397-398, 402, 416, 422, 503, 510
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       88      8     34      3    89%   22-23, 38-42, 70->72, 80
sambinha/inventory.py       82      6     42      5    91%   37, 44, 50, 65, 82, 91
sambinha/logging.py         31      0      6      0   100%
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         60      2     16      1    96%   49-50, 66->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       208     21     86     15    87%   34, 41, 70, 76, 83, 85, 88, 96, 132-135, 145, 183, 199, 222, 230, 237-240
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          117     16     68     17    82%   25, 44, 50, 56, 61, 80, 88, 92, 104, 106, 107->112, 109, 115, 118, 121, 123, 129
sambinha/storage.py         89     12     32      3    86%   65-66, 71-72, 73->75, 92, 94-95, 110-111, 114-116
--------------------------------------------------------------------
TOTAL                     1570     94    640     65    92%
```
