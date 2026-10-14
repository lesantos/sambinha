# Plano de implementação e retomada — Sambinha 0.13.0

Este arquivo é o ponto de retomada persistente do trabalho autorizado pelo usuário em 2026-10-14. Leia-o e confira o estado real do Git antes de continuar; não dependa da sessão de conversa anterior. Atualize este arquivo a cada etapa concluída ou decisão importante.

## Autorização e resultado esperado

O usuário autorizou implementar todos os requisitos abaixo, documentar as funcionalidades nos Markdown do projeto, implementar testes de comportamento, executar toda a suíte e, somente se os testes passarem, criar/publicar o release 0.13.0 e enviar as mudanças ao GitHub. Não é necessária nova confirmação para essas etapas já autorizadas. Não alterar os dados reais de backup ou executar rebalanceamento neles durante testes.

Repositório: `/home/leandro/dev/sambinha`. Versão inicial: 0.12.0. Há alterações anteriores não commitadas (renomeação de verificações, documentação e outras alterações em recuperação/processos/testes); preservá-las e revisá-las, sem reset/revert. Na inspeção inicial havia alterações em `archive_recovery.py`, `process.py` e `test_secrets.py`, além das renomeações; não atribuir essas alterações à nova implementação sem conferir o diff.

## Requisitos aceitos

1. Compressão única para todos os tipos, configurável, padrão nível 3 e duas threads. Remover separação dos lotes por categoria e limite de quantidade de arquivos.
2. Formação inicial por profundidade primeiro: em cada diretório ordenar juntos arquivos/subpastas pelo mtime, do mais antigo ao mais novo; desempatar por nome. Visitar completamente a subpasta antes de seguir para o próximo irmão; arquivos são as folhas adicionadas aos lotes.
3. Lotes normais limitados pela soma dos bytes originais ao serem formados/admitirem novos arquivos. Arquivos grandes são desviados para lotes individuais sem fechar o lote normal em formação.
4. Configurar a criação de lotes individuais por um percentual do tamanho máximo normal. Definir claramente igualdade com o limiar, valores permitidos e padrão.
5. Associação arquivo→lote persistente: arquivo existente permanece no grupo original quando mudar tamanho/data, mesmo ultrapassando o limite normal. Recriar apenas os lotes afetados; preservar a ordem dos membros já associados. Remoção tira apenas o membro; grupos vazios deixam de ser ativos, com histórico respeitando a retenção. Arquivos novos podem preencher um lote normal incompleto sem mover membros existentes; definir política determinística que evite reenvios desnecessários.
6. Alertar quando lotes não individuais atingirem um tamanho configurável. Não interromper execução nem enviar falha durante os jobs: agregar e enviar evento Cronitor de falha somente no final. Manter resumos/logs claros e distinguir sucesso operacional de alerta. Não emitir um sucesso Cronitor que mascare a falha final de alerta.
7. Comando manual de rebalanceamento: colocar os lotes não individuais dentro do limite, alterando o mínimo possível de lotes existentes. Nunca executar automaticamente. Preservar todos os lotes já conformes, tratar somente os excedidos e gerar novas versões/grupos extras necessários. Arquivos que sozinhos não cabem precisam de lotes individuais. Reusar publicação, retenção, proteções e dry-run; documentar a definição de mínimo e os critérios determinísticos usados.
8. Restore independente da existência/montagem/capacidade da origem. Continuar protegendo destino real e saída, caminhos/links e temporários. Não enfraquecer verificações da origem em backup ou comandos que a consultem.
9. Antes de backup com estado local ausente ou inválido, recuperar geração confirmada do índice no destino, em vez de tratar backup existente como vazio. Preservar associação, histórico e retenção; diferenciar destino novo de destino inacessível/índice irrecuperável. Nenhuma mutação destrutiva antes da recuperação validada.
10. Manter gerações anteriores do índice de recuperação no destino; publicar geração somente após lotes/controles completos; recuperar versão válida quando a mais recente faltar/estiver corrompida. Definir publicação e reconciliação após falhas entre estado local e destino, retenção das gerações e compatibilidade com o índice antigo `.sambinha/control.tar.gz`. Recuperação em outra máquina exige configuração/credenciais e identidade compatível.
11. Origem indisponível nunca equivale a inventário vazio e não pode provocar remoção massiva.
12. Manter/terminar renomeações já em andamento: `verify-local-metadata`, `verify-destination-metadata` e conferir o nome atual da verificação de integridade no código antes de documentar.

## Decisões e riscos que devem permanecer explícitos

- Um lote maior aumenta recompressão/reenvio, espaço temporário e volume necessário para restauração parcial; não existe rebalanceamento automático.
- Associação estável é lógica; nome físico da revisão compactada pode mudar. Índice ativo deve conter cada caminho uma única vez; versões anteriores são histórico deliberado.
- Classificação de lote individual deve ser persistente quando necessário, com compatibilidade para controles antigos; um grupo individual não deve receber novos membros após seu arquivo encolher.
- As simulações usaram datas atuais das pastas restauradas; a origem real pode gerar fronteiras diferentes.
- O destino de teste `llld-s3` é atualmente LOCAL: `/mnt/dados/s3`; state/logs/metadados também estão em `/mnt/dados/sambinha`. Perda desse volume exige origem ou outra cópia. Índices não substituem cópias dos dados.
- Não afirmar transação atômica distribuída entre máquina e destino. Usar publicação ordenada, gerações confirmadas e testes de falha/retomada.

## Artefatos anteriores úteis (temporários, podem desaparecer após reboot)

- Simulação: `/tmp/sambinha-dfs-simulation/relatorio.md`, `simulation.json`, `visualizacao.html`, árvores de diretórios.
- Resultado: 31.909 arquivos, 19.291.846.150 bytes; limite 1 GiB. Sequência contínua: 15 lotes. Mantendo lote normal aberto ao desviar grandes: 14 lotes (mínimo de tamanho sem dividir arquivos grandes), dois individuais acima de 1 GiB.
- Análise anterior: 2.645 .7z ativos, 15.625.755.972 bytes compactados. Índice e tamanhos do destino coincidiam.

## Etapas e checkpoints

- [x] Salvar este plano em disco antes de implementar.
- [x] Revisar AGENTS aplicáveis, diffs existentes, arquitetura, configuração, CI e mecanismo de release/GitHub.
- [x] Fechar e registrar nomes/padrões dos novos parâmetros e a semântica precisa de rebalanceamento e gerações.
- [x] Implementar configuração unificada e planejamento DFS por mtime, com desvio de arquivos grandes.
- [x] Implementar associação persistente, crescimento sem rebalanceamento e exclusão de grupos vazios.
- [x] Implementar alertas agregados com falha Cronitor apenas ao final.
- [x] Implementar rebalanceamento estritamente manual e publicação segura.
- [x] Implementar restore sem origem e recuperação de estado/gerações com cenários de falha.
- [x] Implementar testes comportamentais, inclusive estabilidade após alteração/remoção, limites/igualdade, arquivo grande intercalado, alertas/continuação, mínimo de lotes afetados, restauração sem origem, perda/corrupção do estado e índice, falha de publicação/retomada.
- [x] Atualizar documentação pertinente em PT-BR/EN, exemplos TOML, arquitetura/especificação, resultados de testes, changelogs e migração 0.12→0.13; buscar referências obsoletas.
- [x] Atualizar versão para 0.13.0, executar toda a suíte/checagens exigidas no CI, cobertura e integração HTTP S3 habilitada; registrar comandos e resultados exatos.
- [x] Revisar diff completo, auditar segredos segundo scripts do projeto, construir e validar wheel/sdist.
- [x] Criar commit(s), tag de versão e release GitHub 0.13.0 com notas/artefatos, enviar branch/tag ao remoto autorizado.
- [x] Confirmar publicação e registrar links, hashes e resultados finais neste plano.

## Como retomar após interrupção

1. Ler este arquivo, `git status --short`, `git diff --stat`, histórico recente e tags; verificar quais tarefas já chegaram ao disco/Git/remoto.
2. Revisar checkpoints/notas abaixo e os arquivos efetivos. Não repetir publicação/tag/release se já existirem; inspecioná-los primeiro.
3. Consultar testes/artefatos persistidos; repetir testes interrompidos, sem presumir sucesso a partir de arquivos parciais.
4. Prosseguir das etapas pendentes dentro da autorização acima. Preservar quaisquer novas alterações do usuário.
5. Atualizar este arquivo com decisões, comandos, resultados e próximo passo antes de operações longas ou publicação.

## Diário de execução

### 2026-10-14 — início

- Plano criado antes de iniciar implementação 0.13.0.
- Inspeção inicial: projeto em 0.12.0, árvore com alterações anteriores não commitadas. Nenhuma implementação dos novos requisitos feita ainda.
- Próximo passo: revisar arquitetura completa, configuração, notificações, índices, testes e release; confirmar parâmetros de limiar/alerta enquanto progride na revisão.

### Decisões confirmadas

- Usuário confirmou: `individual_batch_percent = 100` e `batch_size_alert_percent = 200` por padrão. Comparação inclusiva (a partir de); tamanho original não compactado. Alerta 0 desativa; limiar individual positivo e até 100%.
- Opções únicas: `compression_level = 3`, `compression_threads = 2`; parâmetros antigos por categoria/max_files serão removidos da configuração, com guia de migração.
- Rebalanceamento manual operará no backup confirmado, mesmo sem origem; mínimo significa preservar todos os lotes ativos conformes e alterar apenas os excedidos. Sobras de excedidos poderão compartilhar novos lotes.
- Novos arquivos entram em novos grupos: não reabrir lotes confirmados por inserções. Isto minimiza reenvios e mantém associação; formação inicial continua usando o lote normal aberto ao desviar grandes.
- Gerações do índice terão objetos imutáveis e marcador de confirmação; journal local permitirá reconciliação após interrupção. Manter padrão de 3 gerações confirmadas (`recovery_index_versions`). Não reter snapshots dos dados indefinidamente: arquivos históricos continuam seguindo retenção; gerações antigas que referenciem dados expirados não serão usadas como fallback válido.
- Repo `lesantos/sambinha`, branch `main`, remoto HTTPS. Ferramenta gh disponível, cobertura/moto instalados; build ainda não instalado. CI pede cobertura >=85% e suíte HTTP S3 habilitada.

### Implementação em andamento — primeiro checkpoint

- Núcleo `batches.py`: grupos persistentes, IDs lógicos, limiar individual inclusivo, desvio sem fechar lote normal, packing sem limite de quantidade/categoria, rebalanceamento só dos grupos excedidos.
- Inventário `scan` alterado para DFS com irmãos ordenados por mtime/nome. Compressão única 3/2.
- `index_generations.py` adicionado: gerações imutáveis confirmadas por SHA-256/tamanho, journal de publicação, marcador de inicialização para retries do primeiro backup, bootstrap validado do destino, fallback e limite de gerações. Ainda precisa testes/revisão de erros e perda de destino.
- `rebalance.py`: manual a partir dos lotes confirmados, sem origem; extrai somente excedidos e usa publicação/retention compartilhadas. `batch_alerts.py`: coleta avisos e engine envia falha final sem mudar sucesso operacional.
- Restore/downloads começaram a separar verificações da origem das do destino. Testes reais sem origem ainda pendentes.
- Primeira validação: 46 testes de planner/config/performance unitários passaram. Algumas fixtures antigas já adaptadas; nenhuma suíte completa passou ainda.
- Próximo passo: implementar testes 0.13, rodar integração local/recuperação e resolver regressões; depois documentação, CI completo, build/auditoria/release.

### Checkpoint: testes novos e ajustes de recuperação

- Testes unitários iniciais: 46 passaram; exemplos TOML bilíngues: 5 passaram; planner/limiares/validação de gerações: 27 passaram após corrigir listagem inválida.
- Testes novos de integração: 18 rodaram, 17 passaram; falha de rebalanceamento por truncamento de mtime de 7z foi corrigida reinstalando metadados originais antes da recompressão.
- Integração local/recuperação/metadados: 72 rodaram, com quatro problemas (três eram expectativas antigas/mocks; um era reconciliação do índice de compatibilidade após falha de publicação). Os quatro casos foram corrigidos e reexecutados: 4 passaram.
- Documentação detalhada criada: `docs/batch-lifecycle.md` e `.pt-BR.md`; guias/especificações/exemplos/migração/changelog sendo atualizados. Versão dos metadados do pacote alterada para 0.13.0.
- `gh auth status` confirmou conta lesantos e remoto/release 0.12.0 existentes. Dependência `build` instalada no .venv; gitleaks não encontrado ainda. Nenhuma publicação 0.13.0 feita.
- Suíte completa exploratória está/estava rodando com saída em `build/validation-0.13.0/full-first.log` (processo de ferramenta 73372). Testes foram alterados depois do início; essa rodada NÃO será a validação final. S3 exploratório em `s3-first.log` (78349).
- Próximos passos: coletar resultados exploratórios, corrigir regressões, testar mudanças S3 adicionais, revisar documentação/diff e executar CI final com cobertura + HTTP S3 habilitado. Preservar os logs em build/validation-0.13.0 (gitignored, persistentes neste disco).

### Checkpoint: S3 e ferramentas de release

- Transporte S3 emulado: 23 testes existentes passaram; 3 novos testes (crescimento+rebalanceamento sem origem, recuperação de estado+restore sem origem, perda completa do conteúdo do destino com origem sobrevivente) passaram. Logs `s3-first.log` e `s3-new.log`.
- Planner/config/exemplos/validação de índices: 53 testes passaram.
- Corrigidos: rebalanceamento reinstala mtime_ns/modo originais após extração; bootstrap marca index_pending para republicar compatibilidade; listagens de recuperação exigem array válido; confirmações JSON inválidas fazem fallback seguro; journal só é removido após concluir limpeza de gerações.
- Gitleaks 8.30.1 baixado/verificado conforme versão do CI em build/tools/gitleaks (SHA do tar: 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb). Auditoria ainda pendente.
- Pendente: resultado da suíte exploratória completa; rodada final de cobertura/HTTP S3; revisão de docs restantes, auditoria/build, commit/tag/GitHub release. Não houve publicação.

### Checkpoint: revisão de publicação e reparo

- Reparo de JSON ausente agora reenvia somente o controle confirmado, sem recomprimir/sobrescrever um compactado saudável (inclusive se os ajustes de compressão mudarem). Casos de reparo+rebalance+perda S3: 3 passaram (`repair-and-rebalance.log`).
- Sequência de gerações agora é monotônica mesmo se o relógio do sistema recuar; validação do estado exige formato seguro para geração. Teste novo adicionado.
- Auditoria do histórico Git: 28 commits, nenhum vazamento (`history-secrets.log`). Auditoria das alterações staged/commit final ainda pendente.
- HTTP S3 está em execução (19844), com log verbose `http-s3.log` e código final em `http-s3.exit`; primeiros casos já passaram. Suíte exploratória completa (73372) ainda silenciosa por usar -q, não presumir conclusão.
- Documentação atualizada nos guias e exemplos, novos padrões confirmados; cabeçalhos de README/pacote agora 0.13.0. Resultados de testes em docs só devem receber números finais após rodada final.

### Resultado da exploração completa (não final)

- A primeira suíte terminou: 410 testes em 1227,398 s, 9 falhas/1 erro/25 HTTP ignorados. A maior parte já estava corrigida em rodadas focadas (mtime rebalance, compatibilidade pós-falha, estado corrompido agora recuperável, limiar não dispara rebuild, mock de listagem).
- Outros casos eram fixtures antigas: teste de remoção reutilizava destino simple como batch; teste de índice reservava destino preexistente sem índice; teste de falha de recuperação bloqueava só índice legado, deixando novas gerações válidas. Fixtures ajustadas para manter os objetivos originais sem enfraquecer a proteção de destino não reconhecido/fallback.
- Próximo: validar esses três testes focados, depois executar UMA rodada final completa com SAMBINHA_HTTP_S3_TESTS=1 e cobertura, em modo verbose e com status persistido.

### HTTP S3 exploratório e último ajuste antes do CI final

- HTTP real: 28 testes, 27 passaram. A perda total de objetos expôs uma diferença real do rclone: lsjson --stat pode retornar null para objeto ausente, em vez de erro not found.
- Substituída checagem S3 por objeto por UMA listagem Path/Size por backup com lotes reutilizáveis, sem modtime/mimetype; isso reconhece objetos ausentes com segurança e evita milhares de HEAD/processos para lotes legados. Falha de acesso/listagem continua fatal.
- Fixtures de destino não reconhecido/fallback já corrigidas; os 3 casos focados passaram (`legacy-fixtures.log`).
- Próximo passo imediato: retestar o caso HTTP de perda do destino, depois executar cobertura completa final com HTTP habilitado. Nenhum release publicado ainda.

### Rodada final iniciada

- Ajuste HTTP de perda do destino retestado: passou (`http-lost-destination.log`).
- Schema de estado novo = 2, com leitura legada schema 1; versões antigas falham com segurança e versões futuras desconhecidas não sofrem downgrade automático. Gerações monotônicas toleram relógio recuando.
- Código funcional congelado para validação final. Comando: `SAMBINHA_HTTP_S3_TESTS=1 COVERAGE_FILE=build/validation-0.13.0/.coverage .venv/bin/python -m coverage run --branch -m unittest discover -v`. Saída: `build/validation-0.13.0/all-tests.log`; código de saída: `all-tests.exit` (só existe após terminar). NÃO publicar se não for 0 ou se houver falhas/skips inesperados.
- Após suíte: relatório de cobertura com o mesmo COVERAGE_FILE, mínimo 85%; revisão/auditoria staged, build/smoke de wheel/sdist, commit/tag/push/release e checagem do GitHub.

### Revisão pré-publicação ainda pendente

- O leitor limita state/control JSON a 64 MiB e total de controles a 256 MiB. Antes de publicar, adicionar ao produtor uma validação dos MESMOS limites para nunca confirmar um índice que o próprio leitor rejeitaria (casos de muitos arquivos/metadados). Testar com limites reduzidos por mock, sem alocar centenas de MiB. Documentar esses limites de metadados, distintos do limite de membros removido.
- A rodada 71784 foi iniciada antes dessa pequena validação adicional; se o código mudar após ela, executar os testes afetados e confirmar suíte completa no CI do commit exato antes de criar o release. Não afirmar que uma rodada anterior cobriu código ainda não existente.
- Planner real validado contra a simulação persistida: 14 lotes / 31.909 arquivos / 19.291.846.150 bytes (`simulation-check.json`).

### Bloqueador de revisão adicional antes de publicar

- Rebalanceamento está usando Selection(None), o que pode ignorar exclusões ao expirar histórico. Corrigir para avaliar as regras configuradas em árvore temporária, sem consultar a origem, e congelar as regras do job também no modo rebalance. Acrescentar regressão: histórico vencido protegido por exclusão deve continuar no destino após rebalance com origem ausente. Esta correção e o guard de tamanho de metadados são obrigatórios antes do release.
- A suíte local completa em andamento cobre o candidato anterior; após esses ajustes, rodar regressões e exigir CI completo no commit final exato antes de publicar o release.

### Suíte completa aprovada e correções finais de revisão

- Rodada completa local com HTTP habilitado: **425 testes, 961,016 s, OK, zero skips**, cobertura **94%** (logs all-tests.log/all-tests.exit=0/coverage-before-review.log). Esta rodada precedeu os quatro novos testes de revisão abaixo.
- Regressões vermelhas reproduziram: expiração indevida de histórico excluído no rebalance sem origem e publicação de estado/controles grandes demais.
- Corrigidos: congelamento de exclusões também no rebalance, avaliação das regras sem consultar origem, preservação de histórico excluído; produtor valida 64 MiB state/controle e 256 MiB de controles antes de confirmar geração. Testes adicionais incluem limite agregado.
- CI atualizado para rodar cobertura com HTTP S3 habilitado na suíte completa, sem rodada HTTP duplicada. Exigir CI verde no commit final exato antes do release.
- Próximo: regressões finais e verificação com cobertura; reconstruir artefatos finais (draft é anterior às correções), auditar/stage/commit/push para CI, publicar somente com CI aprovado.

### Revisão corrigida e preparação do candidato

- Quatro regressões de revisão agora passam junto de planner/config/exemplos/índices: 58 testes, 26,003 s, OK (`review-regressions-green.log`, exit=0). Os casos vermelhos anteriores estão em review-regressions-red.log.
- Código/app/testes/CI congelados novamente. Coleção final tem 429 testes (425 anteriores + 4 novos); exigir execução completa verde do candidato no GitHub.
- Build draft foi construído e instalado em venv isolado, importando 43 módulos de site-packages e verificando CLI/padrões; NÃO usar os artefatos draft para release, pois antecedem os quatro ajustes de revisão. Rebuild final agora.
- Próximos passos: build final/smoke/hashes; git add + gitleaks staged; commit/push main; aguardar CI no SHA exato; só então tag v0.13.0 e gh release create com wheel/sdist/SHA256SUMS. Atualizar este plano com URLs/hashes e status final.

### Candidato final pronto para commit/CI

- Build final 0.13.0 concluído em `dist/0.13.0`; wheel/sdist conferidos byte a byte contra os 45 módulos fonte atuais. Instalação isolada final validou versão, importação de 43 módulos, CLI e padrões confirmados (3, 2, 100%, 200%).
- SHA-256 wheel: `148ed10a220c1844eca25673a3142354221cd880a3779032d01cb41e2a2f4d50`; sdist: `a0918be9af8595f141654d4e11a9bf5fe0f517ec1575e9ced8b8acd50c61d12f`. Arquivo SHA256SUMS junto dos dois artefatos.
- Todos os bloqueadores de revisão acima foram corrigidos e as regressões passaram. Aguardando somente commit/push e CI completo do SHA final para autorizar tag/release.
- `git add -A` requereu execução escalada porque .git é somente leitura no sandbox; staging concluído. Auditoria gitleaks staged em andamento; repetir se houver nova alteração além deste plano.

### Commit publicado; aguardando CI

- Commit candidato enviado a `origin/main`: `9ec723f2387f8d7fa06dfb07eebc1f293f6de4f6`. Gitleaks staged e histórico completo (29 commits) sem vazamentos.
- Workflows do candidato: https://github.com/lesantos/sambinha/actions/runs/38633741928 e https://github.com/lesantos/sambinha/actions/runs/38633752634. Conferir nome/conclusão de cada um; exigir Tests verde antes de tag/release.
- Os pacotes finais permanecem em `dist/0.13.0` com hashes registrados acima. Nenhuma tag/release 0.13.0 criada ainda. Se retomar, consultar os workflows e remoto antes de repetir ações.

### CI final aprovado — publicação autorizada

- Workflow Tests do commit exato `9ec723f2387f8d7fa06dfb07eebc1f293f6de4f6`: **success**. https://github.com/lesantos/sambinha/actions/runs/38633741928
- **429/429 testes em 171,741 s, zero skips/falhas**, HTTP S3 habilitado. Cobertura da aplicação incluindo branches **94%** (3097 statements, 143 miss, 1176 branches, 119 partial). Log persistido: `build/validation-0.13.0/github-tests.log`. Dependency Graph também passou.
- Notas bilíngues finais prontas em `build/validation-0.13.0/release-notes.md`. Próximo: tag anotada v0.13.0 no commit testado; push tag; publicar wheel/sdist/SHA256SUMS; verificar assets; atualizar docs/resultados e plano em commit somente documental.

### Concluído — release publicado e verificado

- Release estável publicado: https://github.com/lesantos/sambinha/releases/tag/v0.13.0
- Tag anotada `v0.13.0` no commit testado `9ec723f2387f8d7fa06dfb07eebc1f293f6de4f6`; tag object `34f9246dfec9ac9aa5383f7a45d13b0a36aafc6e`. Main e tag enviados ao GitHub.
- Assets publicados e baixados novamente para `build/validation-0.13.0/published-assets`: wheel, sdist e SHA256SUMS; ambos os pacotes conferem com os hashes finais registrados acima. Release não é draft nem prerelease.
- Guias bilíngues de resultados receberam os números e o link do CI final. Este plano e esses resultados seguem em um commit posterior exclusivamente documental; a tag permanece no commit funcional aprovado, sem retag.
- Implementação, documentação, testes completos, auditoria, build e publicação concluídos. Nenhum dado real de backup foi alterado. Não há implementação pendente.
- Para conferir o envio do fechamento documental após uma interrupção: `git status --short`, `git log -2 --oneline` e `git ls-remote origin refs/heads/main`. Se os três Markdown de fechamento ainda estiverem modificados, commitá-los e enviar main; nunca recriar o release já publicado.
- Atenção operacional documentada: migrar TOMLs antigos removendo max_files e compressão por categoria antes de usar 0.13.0. Configurações reais fora do repositório não foram modificadas nesta tarefa.
