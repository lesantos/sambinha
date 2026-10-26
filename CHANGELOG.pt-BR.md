# Novidades do Sambinha

## 0.15.0 — 2026-10-26

Executar também no macOS, além do Linux. Ler a tabela de montagens pelo comando BSD `mount` quando `/proc/self/mountinfo` não existe, classificar APFS e HFS+ como volumes POSIX e `msdos` como FAT, e conferir `source_mount`/`destination_mount` comparando dispositivos com o diretório pai onde o `mountpoint` do util-linux não existe. Usar um descritor somente leitura e não bloqueante no lugar de `O_PATH` nos inventários só de metadados. Adicionar `tests/test_macos.py` com testes de plataforma simulada, que rodam em qualquer sistema, e testes reais, que rodam apenas em um Mac. Documentar instalação pelo Homebrew, pontos de montagem em `/Volumes`, APFS sem distinção de caixa, Acesso Total ao Disco e agendamento por `launchd`. O comportamento no Linux não muda.

## 0.14.9 — 2026-10-26

Verificar as versões dos programas externos antes de usá-los. Backups, restaurações e verificações agora param com um erro claro quando o rclone é anterior à 1.60, e as operações em lotes param quando o compressor é anterior ao 7-Zip 22.00 (a primeira versão cuja listagem mantém frações de segundo nas datas; 21.07 e p7zip 16.02/17.x não mantêm). Versões que não puderem ser lidas são aceitas. Corrigir o mínimo documentado de 7-Zip 21 para 22.00, informar os dois mínimos nos READMEs e nos guias de instalação e deixar de recomendar os pacotes antigos `rclone`/`p7zip-full` do apt no Ubuntu 22.04. Adicionar testes unitários e de integração com executáveis falsos.

## 0.14.8 — 2026-10-26

Fazer `verify_source_changes = false` também ignorar as checagens de identidade de diretórios no inventário da origem; em montagens CIFS com `serverino` o inode de um diretório pode mudar entre a listagem e a abertura, o que interrompia backups com "Diretório mudou durante leitura". Forçar `RCLONE_LINKS`, `RCLONE_LOCAL_LINKS`, `RCLONE_COPY_LINKS` e `RCLONE_LOCAL_COPY_LINKS` como `false` em todo subprocesso, pois o rclone 1.75 deixa essas variáveis sobrepor `--links=false`/`--copy-links=false` e links simbólicos da origem poderiam ser copiados. Fazer os testes sensíveis a timestamps esperarem o relógio do sistema de arquivos avançar (Linux 5.15/ext4 marca mtime/ctime de forma grosseira). A suíte completa exige rclone 1.60+ e 7-Zip 22.00+ (`7zz`); os pacotes da distribuição (rclone 1.53 e p7zip 16.02) falham em testes não relacionados.

## 0.14.7 — 2026-10-16

Adicionar `verify_source_changes` por job para controlar checagens de consistência durante a leitura/cópia dos arquivos de origem. Quando ativada, retentar alterações concorrentes até três vezes após a tentativa inicial e falhar o backup se persistirem. Manter a opção ativada por padrão na configuração do aplicativo e nos exemplos; desativá-la na configuração local do Sambinha. Documentar a diferença e cobrir retentativas e desativação.

## 0.14.6 — 2026-10-16

Para arquivos individuais grandes com extensões de formatos compactados reconhecidos, enviar o arquivo original diretamente e restaurá-lo/verificá-lo sem compactá-lo novamente. Adicionar regressões do planejador e de restauração local completa.

## 0.14.5 — 2026-10-16

Mover `temp_dir` dos jobs para a configuração compartilhada `[backup]`, junto dos outros diretórios configurados. Todos os jobs de uma configuração de backup passam a usar o mesmo diretório temporário existente e gravável; caminhos relativos são resolvidos a partir do arquivo TOML. Atualizar validação, exemplos e documentação para refletir a opção compartilhada.

## 0.14.4 — 2026-10-16

Criar lotes diretamente dos arquivos na origem, eliminando a cópia temporária dos originais e reduzindo o espaço de staging para aproximadamente um arquivo compactado por worker. Validar a origem antes e depois da compactação e interromper o envio se os arquivos divergirem do inventário. Processar lotes que excedem `max_megabytes` um por vez. Adicionar `temp_dir` por job para escolher um diretório temporário existente em outro disco, com caminho relativo ao TOML, além de atualizar diagnósticos, exemplos e documentação.

## 0.14.3 — 2026-10-15

Corrigir falsos erros de alteração da origem quando exFAT/FAT nativo atribui outro inode entre a descoberta e a abertura de um arquivo ou diretório ainda não mantido aberto. Identificar os dispositivos afetados pela tabela de montagens atual, incluindo montagens internas; aceitar somente diferença no inode quando dispositivo, tipo, tamanho, mtime, ctime e permissões coincidem. Informações de filesystem desconhecidas ou ambíguas mantêm a validação estrita.

Usar o descritor aberto como referência do diretório até conferir novamente seu caminho. Manter arquivos abertos durante a análise de metadados/hashes e as checagens finais do descritor/caminho; o modo simples usa `O_PATH` e nunca lê conteúdo. Preservar comparação estrita de identidade enquanto os objetos estão abertos e reaproveitamento conservador de fingerprints por inode. Mensagens de erro passam a incluir a etapa da comparação e os valores completos antes/depois. Links da origem continuam ignorados, e a descoberta continua sequencial. Adicionar regressões de mudança de inode, substituições, diagnóstico, fechamento de descritores e detecção por dispositivo; validar uma mudança controlada de inode histórico em uma montagem exFAT real somente leitura.

## 0.14.2 — 2026-10-15

Unificar descoberta com filtros nativos e inventário batch em um percurso sequencial DFS por data/nome, analisando os arquivos conforme cada diretório é descoberto. Usar descritores de diretórios abertos e operações relativas sem seguir links, em vez de repetir verificações dos ancestrais para cada arquivo. Links simbólicos na origem, incluindo diretórios ligados, links quebrados e ciclos, continuam ignorados; seus alvos nunca são inventariados ou copiados através do link. Preservar assinaturas do cache de hashes, detecção de alterações concorrentes, exclusões nativas e confirmação somente após inventário completo.

Usar uma chamada HTTP síncrona `operations/list` por diretório, eliminando consultas e pausas de acompanhamento dos jobs assíncronos. Manter prazo independente por diretório e encerramento/recolhimento do servidor privado do rclone em falhas ou interrupções. Aplicar o percurso unificado também à verificação local de metadados. Adicionar regressões de inventário/alterações concorrentes e benchmark reproduzível de leitura antes/depois.

## 0.14.1 — 2026-10-15

Exibir o caminho do diretório ou arquivo atual no final da linha de progresso, depois do percentual, etapa e contadores de atividade. Manter as contagens antes dos caminhos de tamanho variável nos formatos completo e compacto. Preservar o ajuste à largura do terminal, tratamento de Unicode, ocultação de dados sensíveis e progresso sem percentual. Adicionar regressões de layout e atualizar a documentação bilíngue.

## 0.14.0 — 2026-10-15

Descobre origens batch com exclusões usando um único processo local privado e autenticado `rclone rcd`. Lista um diretório por vez com `operations/list`, aplicando filtros nativos relativos à raiz original da origem, sem iniciar rclone e construir árvores temporárias de filtros em cada diretório. Aplica o timeout configurado independentemente a cada diretório; cancela operações pendentes e encerra/recolhe o servidor em falhas, timeout ou interrupções. Nunca confirma um inventário parcial. Mostra contadores de diretórios/arquivos na descoberta e mantém a avaliação de nomes históricos.

Documenta o glob `Icon[\x0d␍]` para arquivos Icon do Finder/macOS, incluindo a representação visível de CR pelo rclone, sem desativar a otimização de filtros de diretórios por `{{...}}`. Adiciona regressões para padrões nativos, poda, travessia com um processo, links, autenticação, timeout, respostas inválidas, interrupção e preservação do estado confirmado. Backups existentes, configuração e formatos dos arquivos continuam compatíveis.

## 0.13.3 — 2026-10-15

Torna independentes por job as opções de retenção, verificação, logs, alertas de exclusão, cache de fingerprints, concorrência de transferências e lotes, histórico do índice de recuperação e simulação. Adiciona `control_storage_class` para objetos JSON/de controle no S3; `storage_class` continua aplicável aos arquivos de lote `.7z`. Controles usam `STANDARD` por padrão, enquanto os lotes usam a classe configurada. Move as opções para cada job nos exemplos TOML públicos.

[English](CHANGELOG.md)

## 0.13.2 — 2026-10-15

Mantenha no máximo `batch_workers` tarefas de lote em andamento e interrompa o agendamento dos pendentes após qualquer falha de worker, inclusive quando um worker anterior ainda estiver ocupado. Aguarde a limpeza dos lotes em andamento e preserve a publicação transacional de estado/índice.

Remova as cópias dos originais após validar integridade e membros do arquivo compactado, antes dos envios. Estime o espaço temporário pelos maiores lotes reais que podem coexistir (originais, arquivo sem ganho de compressão e margem), incluindo grupos excedidos e arquivos individuais, e confira novamente antes de copiar cada lote. Preserve a checagem de ocupação acima de 95% e as checagens temporárias obrigatórias. Atualize a documentação bilíngue e acrescente regressões de S3 bloqueado, interrupção após falhas, limpeza e capacidade.

## 0.13.1 — 2026-10-14

Troca `max_bytes` por `max_megabytes` e `--max-bytes` por `--max-megabytes`. O limite passa a ser informado em MB (1 MB = 1.048.576 bytes), com padrão 1024. Aceita decimais; atualiza exemplos e instruções de migração preservando o tamanho dos lotes.

## 0.13.0 — 2026-10-14

Adota lotes persistentes com IDs lógicos, DFS por data/nome e compressão única (nível 3, duas threads). Remove o limite de quantidade e fronteiras por categoria. Arquivos a partir de `individual_batch_percent` (100%) são desviados sem fechar o lote normal. Alterações mantêm membros mesmo acima de max_bytes; grupos vazios seguem retenção.

Adiciona alertas `batch_size_alert_percent` (200%; 0 desativa) para lotes não individuais, agregados em falha Cronitor apenas ao final, mantendo o retorno operacional bem-sucedido. Adiciona `rebalance` manual, independente da origem, que altera só grupos excedidos e preserva conformes/individuais.

Restauração deixa de depender da origem/montagem/capacidade da origem. Backups recuperam estado ausente/corrompido antes de planejar. Gerações imutáveis do índice com confirmação SHA-256/tamanho, journal durável e fallback validado permitem retomada após interrupções; `recovery_index_versions = 3` limita gerações de metadados. Preserva leitura de controles/índices legados, histórico e proteção contra caminhos duplicados. Documenta migração e limites de recuperação.

Renomeia `integrity-test` para `verify-destination-integrity` e a função Python para `verify_destination_integrity_job`; o nome antigo deixa de ser aceito na CLI. Compara datas dos lotes e do índice em UTC para evitar falsos erros causados por diferenças históricas de horário de verão. Aplica `TZ=UTC` apenas aos subprocessos de listagem, preservando o ambiente configurado. Adiciona regressões reais 7z/ZIP em vários fusos e atualiza comandos e documentação bilíngue.

Renomeia `verify` para `verify-local-metadata` e `verify-metadata` para `verify-destination-metadata`, incluindo as funções Python dos jobs. Atualiza comandos, testes e documentação bilíngue. Os nomes antigos da CLI deixam de ser aceitos; ajuste os comandos agendados. Preserva o comportamento das verificações, a configuração e os nomes dos eventos de log.

## 0.12.0 — 2026-10-14

Restaura dados batch no disco de saída, com limpeza dos temporários por lote, publicação protegida e suporte a sistemas sem atributos POSIX ou links físicos. Downloads e extração deixam de ocupar o temporário padrão da máquina.

Separa `verify` diário (caminhos/tamanho/data da origem contra JSONs locais, sem consultas S3), `verify-metadata` mensal (sincronia das cópias e metadados de tamanho dos lotes) e `integrity-test` manual (listagem/integridade pelo 7-Zip, sem arquivos extraídos). Adiciona `metadata_dir`, catálogo local com identidade do job, importação do estado local confirmado e publicação atômica dos JSONs. O estado ativo só avança depois de concluir lotes atualizados e controles locais/remotos; JSONs e índices S3 continuam em STANDARD. Documenta perda aceita de precisão das datas, regras de ciclo de vida e agendamento.

Mensagens de parâmetros TOML desconhecidos passam a informar job, nomes dos parâmetros, linhas e caminho do arquivo. Atualiza guias e exemplos completos bilíngues. Acrescenta regressões de verificação diária apenas pelo cache, divergência de metadados, falhas/repetição da publicação, classes S3, precisão das datas, restauração no destino, corrupção e proteção contra sobrescrita/links.

## 0.11.0 — 2026-10-14

Refatora a orquestração em módulos próprios para contexto, ciclo de vida do job, pré-verificações, criação de arquivos compactados e estratégias simples/batch. Dá nomes e tipos aos campos do inventário; separa planejamento de lotes, seleção de envios e execução de workers limitados. Compartilha identidade do índice e consulta aos arquivos ativos com a recuperação, mantendo os pontos de importação existentes do engine. Preserva CLI/configuração, ordem, retenção, esquemas de estado, operações de armazenamento, políticas de erro e telemetria.

Separa leitura do índice de recuperação, validação de arquivos compactados, preparação/publicação e verificação da coordenação de ações. Isola montagem de comandos rclone das proteções de alteração e operações nativas de árvores. Agrupa validação por opções globais, opções de job, caminhos e sobreposição de destinos, preservando ordem e mensagens de erro. Mantém os pontos de entrada existentes de configuração, Storage e funções de recuperação.

Move os mocks dos testes para os módulos responsáveis. Corrige um teste preexistente de filtro inválido para aceitar a raiz vazia já preparada pelo sync nativo, ainda exigindo destino sem entradas, índice não confirmado e notificação de falha; o comportamento da aplicação permanece igual.

A versão também inclui as alterações de links no destino já commitadas abaixo.

Evita a varredura extra do destino em exFAT/FAT nativo sem montagens internas que aceitem links; mantém as checagens de raízes e a varredura conservadora para sistemas desconhecidos.

Nunca cria links no destino. Ignora links na origem e preserva links sem conflito no destino; substitui links conflitantes nos dados locais por arquivos/diretórios reais sem alterar seus alvos, respeitando exclusões e simulações. Reconstrói lotes/controles batch que viraram links e ignora links no histórico batch expirado. Mantém as proteções de links nas raízes do destino e nos metadados. Atualiza e habilita os dois testes de links no destino anteriormente adiados, com novas regressões de links quebrados, exclusões, simulação, reconstrução batch e mudanças concorrentes de diretórios.

## 0.10.2 — 2026-10-13

Adiciona níveis independentes `error`, `summary` e `detailed` para os logs JSONL no arquivo e no terminal. Ambos mostram erros e resumos da execução por padrão. O sync simples passa a solicitar somente mensagens `ERROR` ao rclone por padrão, em vez de `DEBUG` por arquivo; a saída detalhada solicita e registra os diagnósticos do rclone. O progresso interativo recebe estatísticas periódicas sem habilitar as demais mensagens, e o streaming detalhado mantém somente o fim da saída de erro na memória.

O `--update` do rclone continua protegendo arquivos mais novos no destino. No rclone 1.60, o aviso de arquivo ignorado só existe em `DEBUG`: o modo padrão de baixo volume não identifica esses conflitos e não falha por eles. Configure uma das saídas como `detailed` para manter a identificação e falha para revisão manual. O tratamento de conflitos dos lotes não muda.

## 0.10.1 — 2026-10-13

Mantém o progresso dentro da largura do terminal, compacta contadores e abrevia caminhos, incluindo caracteres Unicode largos/combinantes. Limpa linhas refluídas após redimensionamento. Nas fases sem total confiável, mostra atividade em vez de um percentual congelado, sem inventário adicional; retoma o percentual medido quando existem totais de transferência.

Lê o progresso do rclone pelos contadores estruturados do JSON, incluindo estatísticas de uma linha sem o prefixo `Transferred:`. Cópias e sincronizações interativas mostram contadores de arquivos conferidos/copiados/remoções/movimentos e um indicador de atividade com tempo decorrido. Comparação/exclusões não inventam avanço percentual; bytes movidos para o histórico não indicam transferência concluída. Mantém o percentual global sem retrocesso e reserva 100% para conclusão bem-sucedida.

Detecta suporte a atributos POSIX no destino de backups/restaurações. Evita mudanças de proprietário e permissões em exFAT, FAT e sistemas sem suporte conhecido, preservando conteúdo e datas. Mantém os metadados em sistemas POSIX e no S3. A detecção é somente leitura, inclusive nas simulações; restaurações de lotes também deixam de exigir `chmod` nesses destinos.

## 0.10.0 — 2026-10-12

Mantém jobs simples no `rclone sync` nativo e `--backup-dir`; limpa históricos de execuções expirados na etapa final do job, usando o maior valor entre `deleted_days` e `version_days` (o histórico do rclone não distingue exclusões de substituições). Preserva arquivos de destino mais novos que a origem, informa seus caminhos e falha o job para revisão manual. Substitui automaticamente conflitos de tipo arquivo/diretório, preservando o item deslocado no histórico. Adiciona `restore --path` para selecionar arquivos e diretórios atuais nos jobs simples. Recusa destinos sobrepostos entre jobs, mantendo permitido que o destino de um job anterior seja a origem de outro posterior. Mantém `max_delete` absoluto por job e exclusões nativas do rclone.

Atualiza a documentação bilíngue e os testes de expiração local/S3, destinos mais novos, substituições, destinos sobrepostos, restauração seletiva e alertas de retenção batch.

## 0.9.1 — 2026-10-12

Exibir o percentual de transferência informado pelo rclone durante cópias agrupadas interativas, fazendo o progresso geral acompanhar a transferência atual em vez de uma estimativa por tempo. Manter estimativas por tempo para outras operações opacas. Atualizar documentação bilíngue e metadados da release.

## 0.9.0 — 2026-10-12

Executar todo o ciclo de cada job sequencialmente na ordem do TOML: congelar exclusões, preflight, inventário, transferências/retenção, confirmação do estado, verificação agendada e resumo. Jobs posteriores observam gravações anteriores, inclusive origens e arquivos de exclusão recém-criados. Retirar restrições de sobreposição entre origem/destino e destinos de jobs diferentes, localmente e no S3; manter as proteções da própria origem/destino e de estado/logs. Falhas posteriores no preflight não impedem jobs já concluídos.

Retirar inventários compartilhados e tornar os limites de alerta de exclusão independentes por job, como em comandos separados. Usar faixas iguais de progresso por job, com trabalho interno ponderado por bytes. Adicionar integração local/S3 real comparando comandos combinados e separados, caches atualizados, destinos sobrepostos, verificação agendada/manual, restauração e falhas. Documentar remoção/substituição em destinos compartilhados, índices desatualizados, controle remoto de lotes do último escritor, metadados reservados na raiz e dependências de simulações.

## 0.8.0 — 2026-10-12

Ignora links simbólicos na origem/destino sem seguir, copiar ou traduzir seus alvos. Protege cópias e históricos existentes enquanto o caminho correspondente for um link na origem ou no destino. Opções explícitas do rclone mantêm essa decisão mesmo com configurações de ambiente para seguir links. Resume ambos os READMEs e preserva as instruções detalhadas no guia completo de configuração.

Adiciona opções por job para suprimir checagens de disco na origem e no destino, mantendo a verificação dos temporários. Identifica o job nas mensagens, logs e notificações de erro, inclusive no preflight global. Adiciona `continue_on_job_error = true` por job para continuar jobs independentes, ou false para parar na primeira falha, em backups e verificações manuais. Documenta as opções nos READMEs, guias e exemplos bilíngues.

## 0.7.0 — 2026-10-11

Retenção configurável de logs de auditoria: `log_retention_days = 14` por padrão; `0` desativa a limpeza, com override na CLI. Backups, restaurações e verificações normais removem logs reconhecidos e inativos pela idade da última escrita. Logs atuais/abertos, arquivos de outros tipos, links e subdiretórios são protegidos; simulações não limpam logs. O percentual global agora aparece no começo da linha de progresso, antes da etapa e do subdiretório. Exemplos e READMEs bilíngues documentam retenção e exclusões. Esta release inclui todas as mudanças commitadas depois de 0.6.2.

## 0.6.2 — 2026-10-11

O terminal interativo omite eventos por arquivo e mostra o subdiretório em trabalho. O progresso estima o backup inteiro com etapas/tarefas ponderadas e lotes concorrentes, sem voltar ou reiniciar entre diretórios/etapas; só a conclusão bem-sucedida exibe 100%. Logs JSON mantêm todos os detalhes por arquivo. Cópias agrupadas ficam no mesmo diretório. Ambos os READMEs explicam localização do arquivo de exclusões, padrões nativos do rclone, simulação e proteção dos arquivos já copiados que passam a ser excluídos. Configurações públicas incluem o caminho do arquivo e modelos bilíngues sem exclusões ativas.

## 0.6.1 — 2026-10-11

O padrão passa a ser `verification_mode = "simple"`: inventário e comparação por tamanho em bytes e data de modificação em nanossegundos, sem ler o conteúdo para calcular hashes. Use `"hash"` no TOML ou `--verification-mode hash` para SHA-256/MD5 com cache e leituras completas periódicas. Trocar para hash recalcula hashes ausentes; lotes e controles mantêm checksums. Progresso interativo exibe etapa, diretório e percentual estimado no stderr, sem entrar nos logs JSON. Backups interrompidos deixam de registrar término bem-sucedido. Novos testes cobrem modos, precisão de metadados, recuperação e separação entre terminal e logs; documentação e exemplos bilíngues atualizados.

## 0.6.0

Documentação padrão em inglês com versões em português do Brasil de README, changelog, segurança, guias e exemplos públicos comentados. Links recíprocos e testes de equivalência dos exemplos. Versão final 0.6.0.

## 0.5.0

P1–P6 em ordem: cache de fingerprints com leitura completa periódica; cópias simples agrupadas pelo rclone; inventários compartilhados; compactação/envio de lotes com concorrência limitada; cache de filtros por execução; níveis e threads separados para texto, vídeo, foto e outros. Lotes intactos continuam reutilizados.

## 0.4.1

Validação de índices, controles e caminhos; separação entre ativo/histórico; metadados e logs privados; recusa de links nos metadados/lock; ocultação ampliada de segredos; restauração com validação de arquivos compactados e criação exclusiva na saída.

## 0.4.0

Comandos `restore` e `verify`, verificação periódica, bloqueio de remoções em massa e resumos por tarefa. Discos locais geram erro acima de 95%. Verificação S3 confere tamanho/MD5 por metadados, sem baixar objetos ou índices ausentes; lotes antigos sem checksum de envio geram erro explícito. Restaurar conteúdo do S3 faz downloads.

## 0.3.1

Novos lotes agrupam os arquivos de cada diretório da data de modificação mais antiga para a mais recente, com desempate pelo caminho. Lotes existentes intactos são preservados com suas chaves e ordem originais, inclusive os criados em versões anteriores. Só arquivos novos e membros de lotes afetados são reagrupados, respeitando os limites e o formato configurados.

## 0.3.0

`source_mount` e `destination_mount` permitem verificar volumes montados e não vazios antes de copiar ou excluir. Problemas de montagem interrompem o backup e enviam erro ao Cronitor.

O Cronitor avisa sobre exclusões definitivas acima de `deletion_alert_threshold`, sem bloquear o backup. Os logs mostram os arquivos e prazos; a janela é de dois dias (um para retenção de um dia).

`exclude_files` permite ignorar caminhos pelos padrões nativos do rclone. Arquivos excluídos e lotes/históricos que os contêm são preservados, inclusive após mudar a seleção.

Arquivos mais novos no destino são preservados. A tarefa falha antes de alterar os dados, registra os conflitos e envia a descrição ao Cronitor. A proteção cobre cópia simples e datas originais confirmadas no índice de lotes.

O parâmetro opcional `secrets_file` permite carregar credenciais de um arquivo `.env` antes de iniciar o backup. Variáveis já definidas no terminal têm prioridade. Arquivos ausentes ou inválidos interrompem a execução antes de qualquer backup, com mensagens sem valores privados. A opção `--secrets-file` permite substituir ou desativar essa leitura em uma execução.

A leitura usa a biblioteca python-dotenv, instalada junto com o aplicativo, e mantém as variáveis isoladas na configuração e nos subprocessos. Configurações anteriores, sem `secrets_file`, continuam funcionando.

Os exemplos mostram todas as opções públicas de configuração, inclusive as opcionais e o caminho do arquivo de credenciais. A CI também confere o histórico completo do Git com Gitleaks para evitar a publicação de segredos.

## 0.2.0

O projeto passa a se chamar Sambinha. O pacote Python, o comando, as configurações de exemplo, os registros internos e os testes usam o novo nome. O repositório e a versão publicada no GitHub também passam a usar esse nome.

O README foi reescrito para acompanhar o leitor desde a motivação do programa até o primeiro backup, a recuperação dos arquivos e os testes. Os detalhes de configuração e armazenamento na nuvem ficam em guias próprios.

As pastas de controle no destino passam a usar `.sambinha`. Para atualizar backups existentes com segurança, consulte a [orientação de atualização](docs/usage.pt-BR.md#atualizando-para-a-versão-020).

A CI mantém ações com Node.js 24 e verifica backups locais e S3 simulados, cobertura de código e integridade dos arquivos recuperados.
