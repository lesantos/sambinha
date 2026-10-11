# Resultados dos testes do Sambinha 0.6.2

[English](test-results.md)

Execução em 2026-10-11, Linux, Python 3.12.3: **250 testes passaram, sem falhas ou testes ignorados**, em **397.585 segundos**. Cobertura com branches: **93%**, acima do mínimo de 85%.

## Comportamento verificado

- Inventário/cópia por metadados como padrão, comparando tamanho em bytes e data de modificação em nanossegundos, sem ler hashes da origem nem solicitá-los na listagem do destino. Testes cobrem primeiro backup, repetição, mudança só no tamanho ou de um nanossegundo, edição com tamanho/data preservados intencionalmente ignorada e troca entre modos simples e hash.
- Cópia/restauração/verificação local e S3 via HTTP com rclone real no modo simples, proteção do destino mais novo e histórico durável. Lotes mantêm checksums de envio e validação do 7-Zip nos dois modos.
- Progresso global crescente entre etapas/diretórios/tarefas, agregação de lotes concorrentes por bytes e 100% somente após sucesso. O terminal omite eventos por arquivo e mostra o subdiretório real das cópias; a auditoria preserva os detalhes. Frequência limitada, desativado sem terminal e ausente de stdout/logs JSON. Backups interrompidos registram término sem sucesso.
- Todos os testes anteriores de planejamento, recuperação, proteções de remoção/disco, exclusões, montagens, retenção, notificações, segredos, cache/concorrência/perfis e exemplos bilíngues foram repetidos com sucesso. Testes anteriores de conferência de conteúdo escolhem explicitamente o modo hash.

Os testes usam dados temporários, rclone e 7-Zip reais e Moto em processo e via HTTP local. Nenhum serviço real AWS/Cronitor foi usado. Uma medição histórica de 0.6.1 em `/mnt/dados` inventariou **1.031 arquivos / 13.354.222.707 bytes em 3,784 segundos** no modo simples. A simulação com a configuração real passou; as medições não incluem cópia e não são uma garantia geral de desempenho.

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
sambinha/config.py         203      4    112      4    97%   136, 139, 184, 207
sambinha/disks.py           22      1     12      1    94%   15
sambinha/engine.py         527     14    186     20    95%   101, 129->119, 148->150, 160, 169->171, 175->177, 190->166, 196->198, 203-204, 254->256, 272->274, 297->295, 300, 303, 457, 469-470, 474, 480->482, 493, 499, 595, 604
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       88      8     34      3    89%   22-23, 38-42, 70->72, 80
sambinha/inventory.py      128      6     66      5    94%   37, 44, 52, 61, 84, 105
sambinha/logging.py         39      0     10      0   100%
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         71      2     18      1    97%   52-53, 69->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/progress.py        88      0     14      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       235     18    104     17    90%   36, 43, 76, 82, 89, 91, 94, 102, 124, 150->148, 168, 206, 222, 238->240, 248, 256, 263-266
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          119     16     70     17    83%   25, 46, 52, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 117, 120, 123, 125, 131
sambinha/storage.py         96     12     34      3    87%   70-71, 76-77, 78->80, 102, 104-105, 121-122, 125-127
--------------------------------------------------------------------
TOTAL                     1858     91    744     75    93%
```

A release 0.6.2 também valida os caminhos de exclusões nas configurações públicas e no arquivo real, os modelos UTF-8 sem regras ativas e os padrões dos READMEs com rclone real. Os checks finais repetiram testes de subprocessos, progresso e exemplos após a atualização dos metadados de versão.
