# Testes da revisão atual do Sambinha

Execução em 8 de outubro de 2026, no Linux Mint, com Python 3.12.3. Esta revisão acrescenta o carregamento seguro de `secrets_file` à base da versão 0.2.0.

**115 testes passaram, sem falhas nem testes ignorados**, em 580.535 segundos. A cobertura foi de **96%**, incluindo decisões de código. O limite obrigatório de 85% passou.

## O que foi conferido

Foram executados todos os testes de backup simples e por lotes, para diretórios locais e S3. Os arquivos são recuperados e comparados por caminhos completos, tamanho, SHA-256 e igualdade dos bytes. O rclone real acessou um servidor Moto no próprio computador; nenhum teste usou AWS ou Cronitor reais.

Os novos testes verificam o arquivo dotenv com `export`, aspas, comentários, Unicode, BOM, diferentes quebras de linha e valores vazios. Conferem prioridade do terminal, caminhos relativos ao TOML, expansão da configuração, overrides CLI, isolamento entre configurações e passagem de credenciais aos subprocessos.

Também verificam arquivos ausentes, ilegíveis ou fora de UTF-8, nomes inválidos ou repetidos, falta de atribuição, aspas incompletas e caracteres nulos. Arquivos inválidos param antes do backup, inclusive em simulação. Expressões de shell permanecem literais e não executam comandos. Mensagens de erro e logs não expõem os valores de credenciais testados, inclusive quando o backend os devolve em um erro.

Um teste de integração configura o remote S3 inteiramente pelo arquivo de variáveis e executa uma cópia com o rclone real. Outro faz o backup local com secrets e confere o conteúdo recuperado e a ausência das credenciais nos logs.

Os casos anteriores de arquivos vazios, nomes especiais, alterações de mesmo tamanho/data, retenção, limites dos lotes, falhas e retomada continuam passando. Os arquivos gerados têm no máximo 100 KiB; as fixtures versionadas somam 147 bytes e os diretórios temporários são removidos ao final.

## Suítes executadas

| Suíte | Testes aprovados |
| --- | ---: |
| `tests.test_batches` | 11 |
| `tests.test_config` | 11 |
| `tests.test_local_integration` | 28 |
| `tests.test_s3_http` | 11 |
| `tests.test_s3_integration` | 10 |
| `tests.test_secrets` | 25 |
| `tests.test_services` | 19 |

## Como reproduzir

Depois de instalar o aplicativo e as dependências de teste:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                    Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------
sambinha/__init__.py        1      0      0      0   100%
sambinha/batches.py        43      0     18      0   100%
sambinha/cli.py            29      3      4      0    91%   34-36
sambinha/config.py        153      2     84      2    98%   139, 162
sambinha/engine.py        258      8     92      8    95%   50->45, 73, 97-98, 146->144, 149, 152, 250, 255, 272
sambinha/errors.py          2      0      0      0   100%
sambinha/inventory.py      59      3     26      2    94%   42, 48, 58
sambinha/logging.py        18      0      0      0   100%
sambinha/notify.py         20      1      8      2    89%   15->exit, 19
sambinha/process.py        37      0     10      1    98%   37->exit
sambinha/secrets.py        22      0      6      0   100%
sambinha/state.py          50      2     14      2    94%   35, 41
sambinha/storage.py        42      2     12      0    96%   53-54
-------------------------------------------------------------------
TOTAL                     734     21    274     17    96%
```

## Executável instalado

O aplicativo foi reinstalado em seu ambiente Python separado, incluindo python-dotenv. O executável instalado foi verificado com arquivo de secrets relativo ao TOML, expansão da origem, simulação, cópia real e recuperação por bytes. Um arquivo inválido retornou código 2, não expôs o valor privado e não alterou o backup existente.
