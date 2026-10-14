# Resultados dos testes do Sambinha 0.11.0

[English](test-results.md)

## Validação da 0.11.0

Em 2026-10-14, Linux, Python 3.12.3: a execução completa com cobertura de ramos e `SAMBINHA_HTTP_S3_TESTS=1` aprovou **349/349 testes em 847,735 segundos, sem falhas nem skips**. Todos os 23 testes S3 por HTTP passaram. A cobertura da aplicação, incluindo ramos, é **93%**, acima do mínimo de 85%.

A execução cobre o refactor de orquestração e os refactors adicionais de recuperação, transporte de armazenamento e validadores de configuração. Uma execução focada também aprovou 32 testes de configuração/armazenamento/verificação. Comparações de árvores sintáticas confirmam lógica inalterada nas funções extraídas de recuperação/sync nativo e as mesmas verificações de configuração na mesma ordem. O wheel contém os fontes atuais exatos, importa os 39 módulos isoladamente e informa versão 0.11.0. O Gitleaks não encontrou vazamentos no histórico Git nem no conteúdo de trabalho.

A execução anterior ao refactor tinha uma falha: o teste de filtro inválido exigia destino inexistente, embora o sync nativo já criasse uma raiz vazia antes de o rclone interpretar o filtro. O teste agora permite esse comportamento existente, ainda exigindo destino sem entradas, índice não confirmado e notificação de falha. O comportamento da aplicação foi preservado.

Reproduza:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --fail-under=85
```

As seções abaixo preservam os registros de validações anteriores.

## Alterações não publicadas de links no destino

A rodada final direcionada passou em **57 testes**, incluindo detecção de sistemas de arquivos, política de links, serviços de armazenamento, níveis de log e exemplos. Os testes confirmam que FAT/exFAT nativo dispensa a passagem pelo destino, enquanto montagens internas que aceitam links, sistemas desconhecidos e raízes inseguras mantêm a proteção. Os destinos montados `LLLD` e `LLLDBKP` selecionam esse caminho sem varredura.

Os **13 testes de `tests.test_symlinks` passaram, sem skips**. Os dois testes de links no destino anteriormente adiados abaixo foram atualizados e habilitados. Cobrem substituição de conflitos, alvos externos intactos, links sem conflito/excluídos, simulação, reconstrução de lotes/controles ligados, preservação de links no histórico expirado e proteção de raízes/diretórios em mudanças concorrentes. Os resultados seguintes descrevem a tag 0.10.2 já publicada.

## Resultados publicados da 0.10.2

Em 2026-10-13, Linux, Python 3.12.3: a execução completa com cobertura de ramos e `SAMBINHA_HTTP_S3_TESTS=1` executou **339 testes em 761.289 segundos: 337 passaram, sem falhas, 2 ignorados**. Os dois casos preexistentes de conflito/corrida com links no destino continuam adiados. Foram usados rclone/7-Zip reais, Moto em processo/HTTP localhost e um pseudoterminal real, sem credenciais AWS ou Cronitor reais. A cobertura da aplicação incluindo ramos foi **92%**, acima dos **85%** exigidos.

Os novos testes cobrem níveis independentes de log, saída padrão `ERROR` do rclone, streaming detalhado, retenção limitada da saída em memória, falha no callback e preservação de arquivos mais novos no destino no modo padrão. Os testes de documentação/exemplos bilíngues e a varredura do conteúdo com Gitleaks também passaram.

Reproduzir:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -q
.venv/bin/python -m coverage report --include='sambinha/*' --fail-under=85
```

## Resultados históricos: 0.10.1

Em 2026-10-13, Linux, Python 3.12.3: a execução completa com cobertura de ramos e `SAMBINHA_HTTP_S3_TESTS=1` executou **330 testes em 942.539 segundos: 328 passaram, sem falhas, 2 ignorados**. Os dois casos preexistentes de conflito/corrida com links no destino continuam adiados. Foram usados rclone/7-Zip reais, Moto em processo/HTTP localhost e um pseudoterminal real, sem credenciais AWS ou Cronitor reais.

A cobertura da aplicação incluindo ramos é **92%**, acima dos **85%** exigidos. As novas regressões cobrem metadados POSIX não suportados no destino, conteúdo/datas em backup e restauração, estatísticas estruturadas do rclone, atividade de exclusões antes da cópia, arquivos vazios, limpeza em interrupções/timeouts, larguras de terminal, células Unicode e redimensionamento.

Reproduzir:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include="sambinha/*" --show-missing --fail-under=85
```

```text
Name                      Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------
sambinha/__init__.py          1      0      0      0   100%
sambinha/batches.py          56      0     26      0   100%
sambinha/cli.py              48      6      8      1    84%   54-56, 60-62
sambinha/config.py          227      5    128      5    97%   163, 166, 208, 231, 251
sambinha/disks.py            26      0     14      0   100%
sambinha/engine.py          434     11    142     14    96%   74, 108, 136->126, 138->126, 149->151, 174->172, 177, 180, 345-346, 350, 352->369, 362, 368, 440, 495
sambinha/errors.py           18      0      4      0   100%
sambinha/filesystems.py      28      1      6      2    91%   36, 43->31
sambinha/filtering.py       112      9     44      2    92%   24-25, 41-45, 85-86
sambinha/inventory.py       140      7     72      6    94%   39, 46, 54, 65, 94, 101, 120
sambinha/logging.py          79      1     26      2    97%   63->68, 71
sambinha/mounts.py           19      1     12      1    94%   17
sambinha/notify.py           22      1     10      2    91%   17->exit, 21
sambinha/process.py         145      4     42      3    96%   91-92, 108->exit, 147-148, 160->169
sambinha/profiles.py         11      0      2      0   100%
sambinha/progress.py        132      0     20      1    99%   129->131
sambinha/protection.py       21      0      8      0   100%
sambinha/recovery.py        260     40    124     23    80%   37, 44, 53, 64, 68, 77, 83, 90, 92, 95, 103, 124-133, 136->143, 148, 150, 153-156, 173, 201, 203-205, 210-212, 217, 239, 267, 292-294
sambinha/retention.py        41      2     12      0    96%   28-29
sambinha/secrets.py          22      0      6      0   100%
sambinha/state.py           119     17     70     19    81%   25, 46, 52-54, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 114->100, 117, 120, 123, 125, 131
sambinha/storage.py         165     26     72     13    82%   23, 45, 78-79, 81-87, 107, 109-110, 140->131, 153-155, 160, 165->157, 178, 190, 192, 198, 209-212
sambinha/terminal.py         67      1     30      3    96%   29->35, 54, 81->84
---------------------------------------------------------------------
TOTAL                      2193    132    878     97    92%
```

## Resultados históricos: 0.10.0

Em 2026-10-12/13, Linux, Python 3.12.3: `python -m unittest discover -v` executou **306 testes sem falhas**; 26 foram ignorados porque HTTP S3 é opt-in e três casos extremos de links foram adiados. Depois, a suíte opt-in `tests.test_s3_http` passou em **23/23**; o teste atualizado de link na origem e os cinco testes de exemplos/documentação também passaram. Somando as execuções, **304 testes passaram e dois casos de conflito/corrida com links continuam ignorados**. Foram usados rclone/7-Zip reais localmente e Moto, sem credenciais AWS ou Cronitor.

A cobertura não foi recalculada para esta release.

## Resultados históricos: 0.9.0

Executados em 2026-10-12, Linux, Python 3.12.3: **301 testes passaram, sem falhas ou testes ignorados**, em **349.296 segundos**. A cobertura incluindo ramos é **94%**, acima dos 85% exigidos.

## Comportamento verificado

Jobs independentes concluem exclusões, preflight, inventário, transferências/retenção, confirmação do estado, verificação agendada e resumo antes do próximo inventário. Testes locais reais comparam comandos combinados e separados para encadeamentos simples/lotes nos modos simples/hash após adicionar, alterar e remover arquivos. Conferem origens e exclusões recém-produzidas, invalidação do cache persistente em edições com tamanho/mtime iguais, restauração exata e verificação manual/agendada.

Destinos locais/S3 iguais ou aninhados são aceitos. Os testes validam cópias distintas protegidas por exclusões, substituições/remoções posteriores que invalidam índices anteriores e jobs por lotes no mesmo destino cujo controle remoto pertence ao último escritor. Metadados reservados na raiz da origem e dependências não criadas por simulação falham como em comandos separados. Falhas posteriores no preflight preservam trabalho já concluído; políticas de continuação e interrupção continuam cobertas. Limites de alertas contam por job e o progresso global permanece crescente.

Todos os testes anteriores de backup, retenção, compactação, filtros, concorrência, discos/montagens, links simbólicos, trava, recuperação, verificação, ocultação de segredos e progresso foram repetidos. Usam dados temporários, rclone/7-Zip reais e Moto em processo e por HTTP local, sem serviços AWS ou Cronitor reais. Exemplos bilíngues públicos e links recíprocos dos guias também passaram.

## Distribuição

| Suíte | Passaram |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 17 |
| `tests.test_disks` | 10 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_job_errors` | 10 |
| `tests.test_job_independence` | 11 |
| `tests.test_local_integration` | 28 |
| `tests.test_log_retention` | 9 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_progress` | 13 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 15 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 22 |
| `tests.test_s3_integration` | 20 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |
| `tests.test_symlinks` | 7 |
| `tests.test_verification_modes` | 13 |
| **TOTAL** | **301** |

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
sambinha/config.py         204      4    110      4    97%   143, 146, 184, 207
sambinha/disks.py           26      0     14      0   100%
sambinha/engine.py         538     11    194     19    96%   107, 135->125, 156->158, 168, 177->179, 183->185, 198->174, 204->206, 266->268, 286->288, 311->309, 314, 317, 482-483, 487, 489->506, 499, 505, 510, 577
sambinha/errors.py          18      0      4      0   100%
sambinha/filtering.py       93      8     34      3    90%   24-25, 41-45, 74->76, 84
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
TOTAL                     1965     85    794     71    94%
```
