# Testes da revisão de segurança do Sambinha

Execução em 2026-10-08, Linux Mint, Python 3.12.3.

**174 testes aprovados, sem falhas nem testes ignorados**: 169 na suíte completa (1011.446 segundos) e 5 verificações adicionais dos exemplos e da regra de credenciais (0.149 segundos). Os testes adicionais foram incluídos após a execução principal e não mudam o código funcional medido.

A cobertura total foi de **95%**, incluindo decisões de código; o mínimo de 85% passou.

## Mudanças verificadas

Arquivos mais novos no destino são preservados, inclusive se forem idênticos ou se o destino mudar durante a cópia. A tarefa retorna erro, lista os conflitos e encaminha a descrição ao Cronitor. A proteção usa nanossegundos e, na cópia simples, também o `--update` do rclone.

`exclude_files` usa os padrões nativos do rclone. Foram verificados caminhos relativos, regras ancoradas, Unicode, diretórios, alternativas e substituições antigas de arquivo por diretório. Arquivos excluídos não são transferidos ou tratados como apagados; seus históricos e lotes mistos são protegidos. Uma mudança nas regras durante a execução só afeta a próxima execução.

O alerta soma exclusões definitivas entre tarefas e conta arquivos/versões dentro dos lotes. Os testes verificam o limite estrito, a janela de dois dias, a retenção de um dia, a retenção zero, a lista completa de nomes no log e a notificação de erro que não altera o código de sucesso nem a data da limpeza. Históricos protegidos por filtros ficam fora da contagem.

A montagem usa `mountpoint -q` e a presença de uma entrada no volume, como o helper `mounted_nonempty`. Foram testados volumes ausentes, vazios, desmontados e perdidos durante a cópia, além de subpastas legítimas e de origem local para S3. Falhas notificam o Cronitor antes de escrever no caminho de um volume indisponível.

As combinações simples/lotes e local/S3 continuam conferindo caminhos, SHA-256, tamanho e bytes recuperados. O rclone real foi executado contra servidor Moto em localhost. Nenhum teste usou AWS ou Cronitor reais. Os arquivos de conteúdo gerados têm no máximo 100 KiB; o teste de 1001 exclusões usa apenas nomes em memória. Fixtures versionadas somam 147 bytes.

Os exemplos são comparados ao esquema público de configuração, inclusive opções comentadas em cada tarefa. A regra de segurança rejeita URLs Cronitor com credenciais e permite placeholders e referências de ambiente.

## Distribuição

| Suíte | Testes aprovados |
| --- | ---: |
| `tests.test_batches` | 11 |
| `tests.test_config` | 11 |
| `tests.test_examples` | 3 |
| `tests.test_filtering` | 11 |
| `tests.test_local_integration` | 28 |
| `tests.test_mounts` | 11 |
| `tests.test_protection` | 7 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 18 |
| `tests.test_s3_integration` | 17 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |

## Como reproduzir

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
sambinha/__init__.py         1      0      0      0   100%
sambinha/batches.py         43      0     18      0   100%
sambinha/cli.py             29      3      4      0    91%   35-37
sambinha/config.py         169      4     94      4    97%   102, 105, 150, 173
sambinha/engine.py         332     11    116      9    96%   52, 80->70, 106, 132-133, 185->183, 188, 191, 306, 313-314, 318, 335
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       83      8     32      3    89%   22-23, 38-42, 69->71, 79
sambinha/inventory.py       65      4     32      3    93%   35, 42, 48, 58
sambinha/logging.py         18      0      0      0   100%
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         37      0     10      1    98%   37->exit
sambinha/protection.py      21      0      8      0   100%
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py           62      5     26      6    88%   36, 40, 46, 48, 49->54, 51
sambinha/storage.py         56      4     18      1    93%   59-60, 65-66, 67->69
--------------------------------------------------------------------
TOTAL                     1022     43    398     30    95%
```

O aplicativo instalado também foi verificado com filtro, proteção de arquivo mais novo e recusa de montagem inválida. Os novos parâmetros aparecem na ajuda do executável.
