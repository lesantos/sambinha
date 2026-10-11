# Resultados dos testes do Sambinha 0.6.1

[English](test-results.md)

Execução em 2026-10-11, Linux, Python 3.12.3: **244 testes passaram, sem falhas ou testes ignorados**, em **362.171 segundos**. Cobertura com branches: **93%**, acima do mínimo de 85%.

## Comportamento verificado

- Inventário/cópia por metadados como padrão, comparando tamanho em bytes e data de modificação em nanossegundos, sem ler hashes da origem nem solicitá-los na listagem do destino. Testes cobrem primeiro backup, repetição, mudança só no tamanho ou de um nanossegundo, edição com tamanho/data preservados intencionalmente ignorada e troca entre modos simples e hash.
- Cópia/restauração/verificação local e S3 via HTTP com rclone real no modo simples, proteção do destino mais novo e histórico durável. Lotes mantêm checksums de envio e validação do 7-Zip nos dois modos.
- Progresso interativo com etapa/diretório/percentual estimado, incluindo avanço por bytes nos hashes e estimativas limitadas de subprocessos. Frequência limitada, desativado sem terminal e ausente de stdout/logs JSON. Backups interrompidos registram término sem sucesso.
- Todos os testes anteriores de planejamento, recuperação, proteções de remoção/disco, exclusões, montagens, retenção, notificações, segredos, cache/concorrência/perfis e exemplos bilíngues foram repetidos com sucesso. Testes anteriores de conferência de conteúdo escolhem explicitamente o modo hash.

Os testes usam dados temporários, rclone e 7-Zip reais e Moto em processo e via HTTP local. Nenhum serviço real AWS/Cronitor foi usado. Uma leitura separada de `/mnt/dados` inventariou **1.031 arquivos / 13.354.222.707 bytes em 3,784 segundos** no modo simples. A simulação com a configuração real passou; as medições não incluem cópia e não são uma garantia geral de desempenho.

## Distribuição

| Suíte | Passaram |
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
| `tests.test_progress` | 7 |
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
sambinha/config.py         203      4    112      4    97%   136, 139, 184, 207
sambinha/disks.py           22      1     12      1    94%   15
sambinha/engine.py         462     14    168     17    95%   95, 123->113, 149, 156->159, 179->181, 186-187, 219->221, 235->237, 255->253, 258, 261, 406, 416-417, 421, 427->429, 440, 446, 531, 538
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       88      8     34      3    89%   22-23, 38-42, 70->72, 80
sambinha/inventory.py      125      6     66      5    94%   37, 44, 52, 61, 81, 102
sambinha/logging.py         34      0      8      0   100%
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         70      2     18      1    97%   52-53, 69->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/progress.py        61      0     12      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       233     18    102     17    90%   36, 43, 76, 82, 89, 91, 94, 102, 124, 150->148, 168, 206, 222, 238->240, 248, 256, 263-266
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          119     16     70     17    83%   25, 46, 52, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 117, 120, 123, 125, 131
sambinha/storage.py         95     12     34      3    87%   68-69, 74-75, 76->78, 100, 102-103, 118-119, 122-124
--------------------------------------------------------------------
TOTAL                     1754     91    720     72    93%
```
