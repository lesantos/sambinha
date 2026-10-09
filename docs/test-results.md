# Testes do Sambinha 0.2.0

A versão 0.2.0 foi verificada em 8 de outubro de 2026, no Linux Mint, com Python 3.12.3.

**88 testes passaram, sem falhas ou testes ignorados.** A execução completa levou 125.864 segundos e alcançou **95% de cobertura**, incluindo decisões de código.

## O que foi conferido

Os testes fazem cópias e depois recuperam os arquivos. Conferem se todos os caminhos estão presentes e se o conteúdo recuperado é igual, byte por byte, ao da origem. Essa verificação cobre cópias simples e por lotes, para pastas locais e para S3.

Para S3, foram usados o rclone real e um servidor Moto no próprio computador. A suíte também utiliza o Moto dentro do processo para testar as regras de backup sem depender da rede. Nenhum dado foi enviado à AWS ou ao Cronitor reais.

Também passaram verificações de arquivos vazios, nomes com acentos e caracteres especiais, edições que preservam tamanho e data, arquivos apagados, prazos de retenção, limites dos lotes, colisões de nomes, falhas e retomada. Um teste específico confirma que o novo formato de metadados não altera backups de uma instalação anterior.

Os arquivos gerados têm no máximo 100 KiB. Para testar um arquivo maior que o lote, o limite do lote é reduzido para 32 KiB. As fixtures versionadas somam somente 147 bytes; as pastas temporárias são removidas ao final.

## Suítes executadas

| Suíte | Testes aprovados |
| --- | ---: |
| `tests.test_batches` | 11 |
| `tests.test_config` | 11 |
| `tests.test_local_integration` | 27 |
| `tests.test_s3_http` | 10 |
| `tests.test_s3_integration` | 10 |
| `tests.test_services` | 19 |

## Como reproduzir

Após preparar o ambiente descrito no [README](../README.md):

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

O limite mínimo de 85% de cobertura também foi atendido.

```text
Name                    Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------
sambinha/__init__.py        1      0      0      0   100%
sambinha/batches.py        43      0     18      0   100%
sambinha/cli.py            29      3      4      0    91%   34-36
sambinha/config.py        140      5     80      5    95%   45, 134, 140, 147, 155
sambinha/engine.py        257      8     92      8    95%   50->45, 73, 97-98, 146->144, 149, 152, 250, 255, 271
sambinha/errors.py          2      0      0      0   100%
sambinha/inventory.py      59      3     26      2    94%   42, 48, 58
sambinha/logging.py        18      0      0      0   100%
sambinha/notify.py         20      1      8      2    89%   15->exit, 19
sambinha/process.py        26      0      8      1    97%   22->exit
sambinha/state.py          50      2     14      2    94%   35, 41
sambinha/storage.py        42      2     12      0    96%   53-54
-------------------------------------------------------------------
TOTAL                     687     24    262     20    95%
```

## Instalação da versão

O arquivo `sambinha-0.2.0-py3-none-any.whl` foi gerado e instalado em um ambiente separado. O comando `sambinha --version` retornou `0.2.0`. A configuração padrão `sambinha.toml`, a simulação e a recuperação de três arquivos por cópia simples e por lotes foram verificadas usando o executável instalado.
