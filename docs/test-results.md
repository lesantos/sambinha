# Resultado dos testes

Execução da revisão final em **2026-10-08**, Linux Mint, Python **3.12.3**.

**87 testes aprovados, zero falhas e zero testes ignorados**, em 489.793 segundos.

A integração HTTP está incluída nesta execução: o **rclone real** acessou um servidor **Moto** em localhost para testar o transporte S3. Nenhum teste usou AWS ou Cronitor reais.

## Comando executado

```bash
SMABAFIBA_HTTP_S3_TESTS=1 /tmp/smabafiba-test-env/bin/python -m coverage run --branch -m unittest discover -v
/tmp/smabafiba-test-env/bin/python -m coverage report --include='smabafiba/*' --show-missing --fail-under=85
```

## Distribuição

| Suíte | Testes aprovados |
| --- | ---: |
| `tests.test_batches` | 11 |
| `tests.test_config` | 11 |
| `tests.test_local_integration` | 26 |
| `tests.test_s3_http` | 10 |
| `tests.test_s3_integration` | 10 |
| `tests.test_services` | 19 |

## Cobertura

Cobertura total, incluindo decisões de código: **95%**. Foram executadas 663 de 687 instruções e 242 de 262 ramificações. O limite obrigatório de 85% passou.

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
smabafiba/__init__.py        1      0      0      0   100%
smabafiba/batches.py        43      0     18      0   100%
smabafiba/cli.py            29      3      4      0    91%   34-36
smabafiba/config.py        140      5     80      5    95%   45, 134, 140, 147, 155
smabafiba/engine.py        257      8     92      8    95%   50->45, 73, 97-98, 146->144, 149, 152, 250, 255, 271
smabafiba/errors.py          2      0      0      0   100%
smabafiba/inventory.py      59      3     26      2    94%   42, 48, 58
smabafiba/logging.py        18      0      0      0   100%
smabafiba/notify.py         20      1      8      2    89%   15->exit, 19
smabafiba/process.py        26      0      8      1    97%   22->exit
smabafiba/state.py          50      2     14      2    94%   35, 41
smabafiba/storage.py        42      2     12      0    96%   53-54
--------------------------------------------------------------------
TOTAL                      687     24    262     20    95%
```

## Integridade e casos de borda

As quatro combinações — simples/local, lotes/local, simples/S3 e lotes/S3 — verificam a restauração de todos os caminhos e a igualdade dos bytes com a origem. Nos lotes, cada arquivo compactado é extraído e seus membros são comparados aos controles por SHA-256 e tamanho. Também são conferidos controles remotos, espelho local e índice de recuperação.

A suíte cobre arquivos vazios, nomes Unicode/com espaços/caracteres especiais, arquivos alterados com mesmo tamanho e data, criação/remoção, limites de tamanho/quantidade, arquivo excedente, colisões de nomes, classes S3, reuso de lotes, retenção nos limites, falhas e retomada, simulação, concorrência e configuração inválida.

Os arquivos gerados têm no máximo **100 KiB**. O caso de arquivo excedente usa um limite de lote de 32 KiB. As fixtures versionadas contêm somente **147 bytes** de texto; os dados maiores são temporários e removidos ao final.

## Pacote instalável

O wheel `smabafiba-0.1.0-py3-none-any.whl` foi gerado e instalado em um ambiente Python separado. O executável instalado retornou a versão `0.1.0`; uma configuração TOML temporária executou simulação e backup real de três arquivos, com igualdade de bytes e auditoria de transferência conferidas.

O GitHub Actions configura Python 3.12, instala rclone/7-Zip/Moto, exige pelo menos 85% de cobertura e executa também a integração HTTP/S3.
