# Requisitos do Sambinha

[English](specification.md)

Este documento consolida os requisitos originais de backup e todas as alterações de produto solicitadas na conversa até a versão **0.10.2**. O inglês é a versão padrão; esta versão em português do Brasil deve conter requisitos equivalentes. As seções abaixo definem comportamento e critérios de aceitação; os marcos de release ao final permitem rastrear as decisões. A seção de 0.6.1 atualiza os requisitos anteriores que exigiam hashes em todos os modos.

## 1. Plataforma, arquitetura e configuração

- Executar em Linux, incluindo Linux Mint, e em macOS, com Python 3.12 ou mais recente. Sondagens específicas de plataforma (tabela de montagens, verificação de ponto de montagem, descritores somente de metadados) devem degradar de forma conservadora quando indisponíveis. Os backups não devem exigir privilégios de root.
- Manter código legível, organizado em domínios separados, com comentários explicando comportamentos relevantes. Usar ferramentas Linux existentes, como rclone e um compactador compatível com 7-Zip, quando simplificarem a implementação.
- Manter o projeto no repositório privado `lesantos/sambinha` do GitHub, com README, guias de uso/recuperação, exemplos públicos de configuração, testes e notas de release.
- Validar dependências, origens configuradas, montagens e ocupação dos discos locais antes das transferências. Tarefas independentes devem continuar após uma falha por tarefa; uma falha global de preflight pode interromper a execução antes dos envios.
- Usar configuração TOML com opções globais em `[backup]` e tarefas independentes em `[[jobs]]`. Aceitar arquivo alternativo e sobreposições pela CLI; argumentos omitidos herdam os valores do arquivo.
- Permitir que uma origem tenha várias tarefas, destinos e estratégias independentes. Recusar destinos sobrepostos entre jobs e sobreposição entre origem/destino do próprio job ou com estado/logs. Permitir que o destino de um job anterior seja a origem de outro posterior.
- Resolver caminhos relativos a partir da pasta do TOML e aceitar referências ao diretório pessoal e a variáveis de ambiente.
- Limitar timeouts e fazer uma tentativa inicial de transferência mais, no máximo, três retentativas, com dois segundos entre elas.

## 2. Destinos, estratégias e histórico retido

- Suportar pastas locais, inclusive volumes montados, e Amazon S3. Ambos os destinos devem aceitar as estratégias `simple` e `batch`.
- Copiar somente arquivos ausentes ou diferentes no destino. Usar checksums, sem reduzir a comparação ao tamanho quando um checksum necessário estiver indisponível.
- Backups simples delegam sincronização e exclusões ao rclone; sync local preserva metadados e diretórios vazios quando suportado. S3 não oferece permissões POSIX nativas.
- Nunca criar links simbólicos no destino. Ignorar links na origem. Substituir um link no destino local quando conflitar com dados selecionados do backup, mantendo seu alvo intacto; preservar links sem conflito ou excluídos. Manter as proteções de links nas raízes de metadados e na saída da restauração.
- Se o destino tiver um arquivo mais novo, preservá-lo. Nos lotes, registrar o conflito e falhar a tarefa, comparando datas originais do índice confirmado, não as datas de upload dos arquivos compactados. No sync simples, `--update` preserva o arquivo; o rclone 1.60 só identifica o conflito em `DEBUG`, solicitado quando alguma saída está em `detailed`.
- Jobs simples usam `--backup-dir` para guardar arquivos substituídos/apagados e expiram o histórico pelo maior valor entre `deleted_days` e `version_days`, pois o rclone não distingue esses motivos. Jobs batch retêm arquivos apagados por 10 dias e versões editadas por 2 dias por padrão, com prazos configuráveis.
- Lotes aposentados devem ficar nas chaves imutáveis até expirar. Um lote com arquivos editados e apagados usa o maior prazo aplicável.
- Lotes reativados não podem ser apagados por registros antigos de aposentadoria. Registrar nova tentativa quando a limpeza de retenção terminar localmente, mas a atualização do índice remoto falhar.
- Usar `GLACIER_IR` como padrão S3, com classes configuráveis. Documentar acesso e características de cobrança no código/exemplos/guias, sem preços fixos. A cópia simples S3 exige uma classe legível sem restore assíncrono.
- Guardar controles JSON e `.sambinha/control.tar.gz` em `STANDARD`, mesmo quando os lotes de dados usam classes mais frias.

## 3. Lotes persistentes e rebalanceamento — 0.13.0

- Ordenar irmãos (arquivos e subpastas juntos) por mtime/nome e percorrer em profundidade. Sem categorias nem limite de quantidade.
- Empacotar bytes originais até o limite `max_megabytes` em MB (padrão 1024; 1 MB = 1.048.576 bytes); desviar arquivos a partir de `individual_batch_percent` (padrão 100%) para lotes individuais mantendo o lote normal aberto.
- Preservar grupos e ordem dos membros já confirmados. Crescimento e redução do limite não redistribuem membros; remoção só tira o membro, e grupo vazio segue retenção.
- Cada caminho deve aparecer em apenas um lote ativo; nomes de revisão são imutáveis e o ID lógico persiste. Controles contêm tamanho/data/permissões, hashes quando disponíveis, tamanho/MD5 do compactado e papel individual.
- `rebalance` é manual, funciona sem origem, altera somente grupos não individuais excedidos e preserva todos os conformes. Não reúne lotes antigos conformes.
- Publicar gerações confirmadas do índice após lotes/controles completos e reconciliar journal após falhas. Recuperar estado ausente/inválido antes de planejar.

Aceitação: alteração/remoção não afeta grupos vizinhos; arquivos grandes intercalados não fragmentam lotes normais; crescimento não provoca redistribuição automática; rebalanceamento muda o mínimo necessário de lotes existentes; cobertura exata sem duplicação de caminhos. Veja [regras completas e migração](batch-lifecycle.pt-BR.md).

## 4. Comando de restauração — funcionalidade selecionada 1 / 0.4.0

- Disponibilizar `sambinha restore` para uma tarefa selecionada, restaurando um arquivo, o conteúdo indexado de uma pasta ou todos os arquivos ativos.
- Aceitar seleções literais relativas à origem com `--path` repetível e exigir `--output`. Preservar a árvore original e recusar caminhos ausentes do índice ativo.
- Exigir saída nova ou vazia, separada de origens, backups, estado e logs. Não sobrescrever arquivos existentes nem seguir links simbólicos na saída.
- Preparar o conteúdo selecionado em staging e verificar tamanho original, SHA-256 e MD5 antes de publicar os arquivos solicitados. Restaurar permissões comuns e datas de modificação, sem bits privilegiados.
- Funcionar mesmo sem a origem original. A recuperação simples exige índice local. A recuperação por lotes pode recuperar um índice ausente de `.sambinha/control.tar.gz`, lendo somente um membro regular `state.json` limitado a 64 MiB, sem extração arbitrária do tar.
- A **restauração** S3 pode baixar objetos selecionados; selecionar um membro de lote baixa aquele lote. Documentar custos de recuperação e a necessidade de restore prévio na AWS para `GLACIER`/`DEEP_ARCHIVE`.
- Este comando restaura a revisão ativa. Versões históricas anteriores continuam recuperáveis manualmente pelos controles e caminhos do histórico.
- A simulação deve informar quantidade/bytes selecionados sem baixar o conteúdo selecionado nem criar a saída. Se precisar recuperar um índice de lotes ausente para montar a seleção, esse download do índice pertence à restauração, não à verificação de integridade.
- Falha posterior de publicação/escrita deve retornar erro; arquivos já publicados e validados podem permanecer.

Aceitação: restaurar dados binários e nomes Unicode byte a byte, incluindo seleção e recuperação sem a origem; corrupção deve falhar antes de publicar os arquivos solicitados, e dados existentes na saída devem permanecer intactos.

## 5. Verificações independentes

- `verify-local-metadata`: origem contra JSONs locais por caminho/tamanho/data, sem ler compactados ou consultar S3; `needs_update` informa alterações. Reduzir o limite não exige reconstrução.
- `verify-destination-metadata`: compara controles JSON completos e tamanhos dos compactados no destino; pode baixar controles/índice, mas não arquivos compactados.
- `verify-destination-integrity`: lista/testa compactados pelo 7-Zip sem extração. No S3, baixa e remove um lote por vez; Glacier/Deep Archive exige disponibilidade prévia.
- Jobs simples usam rclone check. `verify_interval_days` só agenda a checagem local depois de backups batch bem-sucedidos, atualizando `last_verified` apenas no sucesso. Nenhuma checagem rebalanceia.

Aceitação: distinguir metadados de conteúdo, respeitar limitações de datas dos formatos, não solicitar restore Glacier automaticamente e preservar estado em erros.

## 6. Proteção contra remoções em massa — funcionalidade selecionada 3 / 0.4.0

- Aplicar `max_delete` absoluto por job aos jobs simples via rclone; manter o limite percentual para batches usando o índice confirmado anterior.
- Usar `--allow-mass-deletion` para ignorar o limite aplicável em uma única execução.
- Limites batch configuráveis retêm os padrões anteriores de 20 arquivos e 50%; `max_delete` simples tem padrão absoluto 100.
- Retornar falha e notificar Cronitor quando rclone exceder o limite; outras tarefas independentes podem continuar.
- Manter este bloqueio de remoção na origem separado do alerta consultivo de exclusão definitiva do histórico.

Aceitação: tarefas simples e por lotes bloqueadas devem manter destino e estado confirmado sem alterações. Casos no limite exato e autorização explícita de uma execução devem obedecer à configuração.

## 7. Monitoramento de ocupação e montagens — 0.4.0

- Monitorar o filesystem da origem e do destino local. Nos lotes, monitorar também staging temporário local; na restauração, monitorar o filesystem de saída.
- Gerar erro quando a ocupação estiver **acima de 95%**. Exatamente 95% é permitido. Incluir espaço indisponível à conta atual no cálculo da ocupação.
- Medir destino ainda inexistente pelo ancestral existente mais próximo, sem criar a pasta do backup apenas para medir espaço.
- Verificar antes do backup, antes de alterações no destino e depois da estratégia, para que a última transferência que ultrapasse o limite seja reportada como falha. Registrar ocupação/bytes livres em `disk_usage` e notificar falhas ao Cronitor quando configurado.
- Não inventar uma porcentagem de ocupação de disco para buckets S3. Em tarefas S3, medir os filesystems locais aplicáveis da origem e dos temporários.
- `source_mount` e `destination_mount` opcional, este último somente local, devem identificar volumes esperados montados e não vazios. Montagens ausentes, vazias, ilegíveis, incorretas ou perdidas devem interromper operações afetadas antes de escrever em volume indevido.

Aceitação: testar abaixo/no limite/acima de 95%, espaço reservado, destinos inexistentes, origem/destino cheios, última transferência ultrapassando o limite e staging local de tarefas S3.

## 8. Logs, simulação, resumos e notificações — funcionalidade selecionada 5 / 0.4.0

- Criar log por execução com início/fim, tarefas/diretórios, arquivos copiados, arquivos idênticos/ignorados, proteções e erros. Usar notificações opcionais configuráveis de ciclo/erro do Cronitor por credenciais externas em `CRONITOR_URL`.
- Disponibilizar simulação que informa o trabalho previsto e grava seu relatório, sem criar estado/lotes de backup, transferir/apagar objetos de backup ou notificar Cronitor.
- Emitir `summary` por tarefa em execução e simulação com sucesso, indicação de simulação, arquivos totais/novos/modificados/intactos/removidos, lotes reutilizados/recriados, bytes originais previstos, bytes de conteúdo realmente transferidos e exclusões históricas pendentes.
- Excluir controles/índice de recuperação dos bytes de conteúdo transferidos. Distinguir bytes originais previstos e bytes compactados transmitidos. Transferências parciais em falha não podem ser relatadas como transação concluída.
- Preservar `deletion_alert_threshold`, padrão 1000: exceder estritamente esse limite dispara aviso consultivo de exclusões definitivas previstas. Contar independentemente por job e incluir arquivos/versões originais dentro dos lotes, sem histórico protegido.
- Usar janela de aviso de dois dias, um dia para retenção de um dia e aviso imediato para retenção zero. O aviso não pode interromper backup, mudar prazos ou transformar uma execução bem-sucedida em código de falha.

Aceitação: resumos devem contabilizar corretamente arquivos adicionados/editados/apagados/intactos, reutilização/recriação de lotes e zero bytes transferidos na simulação; avisos existentes de retenção devem continuar consultivos.

## 9. Revisão de segurança e correções críticas — 0.4.1

- Revisar o código segundo orientações de segurança reconhecidas e implementar as correções aplicáveis mais críticas, documentando escopo e validação por regressão.
- Validar esquema de estado/controles, caminhos relativos seguros, metadados/checksums originais, membros ativos únicos e conflitos entre ativos/aposentados antes da retenção ou recuperação. Recusar travessia, caminho raiz, caracteres nulos/quebras de linha e arquivos especiais não suportados na origem; reservar `.sambinha` na raiz da origem.
- Gravar estado atomicamente e proteger operações de escritor único com lock. Usar diretórios privados de metadados/logs (0700) e novos arquivos de estado/controle/log/lock (0600), independentemente de umask permissivo. Recusar redirecionamento por links nos diretórios de metadados e no lock.
- Manter segredos AWS/Cronitor fora dos arquivos versionados. Ler dotenv privado opcional como dados, nunca comandos de shell; variáveis herdadas têm prioridade. Arquivos ausentes/inválidos devem falhar sem expor valores.
- Ocultar credenciais conhecidas de ambiente/configuração do rclone, URLs explícitas do Cronitor, usuário/senha em URLs e variáveis de senha nos erros de backend/logs/telemetria, inclusive valores multilinha antes da serialização JSON. Deixar o rclone descriptografar sua configuração criptografada.
- Invocar subprocessos sem interpolação de shell; usar listas literais e argumentos seguros de compactação para não interpretar nomes como opções ou curingas.
- Recusar membros inseguros/links antes da extração de restauração, verificar bytes temporários, criar arquivos exclusivamente na saída e retirar bits privilegiados. Ler metadados de tar de recuperação sem extração arbitrária.
- Preservar exclusões nativas do rclone e congelá-las por execução. Arquivos excluídos não podem ser enviados nem tratados como apagados; histórico e lotes antigos mistos que os contêm permanecem protegidos.
- Usar busca automatizada de credenciais na CI, com saída ocultada e download do scanner verificado. Exemplos públicos devem conter somente placeholders ou referências de ambiente.
- Documentar o limite de confiança: permissões privadas/validação não autenticam um índice válido alterado pela mesma conta/root; checagens S3 por metadados não testam toda a extração dos lotes.

Aceitação: testar caminhos maliciosos, índices corrompidos, conflitos ativo/histórico, links de lote/saída, umask permissivo, erros de backend com segredos e compatibilidade com configuração criptografada do rclone.

## 10. Melhorias de performance P1–P6 — 0.5.0

As seis melhorias foram selecionadas para implementação, na prioridade de P1 a P6. Acelerar a execução deve preservar proteções da origem/destino, validação de integridade, exclusões e garantias de confirmação do índice.

| Prioridade | Melhoria exigida | Configuração e aceitação |
| --- | --- | --- |
| P1 | Cache de fingerprints de arquivos intactos. | `fingerprint_cache = true`; reutilização exige dispositivo, inode, tamanho, mtime/ctime em nanossegundos e permissões iguais. `fingerprint_full_scan_days = 7` força leitura completa periódica; `--no-fingerprint-cache` desativa. Editar com tamanho igual e mtime restaurado deve invalidar o cache pelo ctime. |
| P2 | Delegar backups simples ao rclone sync nativo. | Um `rclone sync` percorre a origem, aplica exclusões nativas, controla concorrência e remoções. `max_delete` é absoluto por job; `--backup-dir` guarda arquivos deslocados. |
| P3 | Inventários independentes por job (0.9.0) | Cada job inventaria a origem quando começa, com seu próprio cache persistente. A leitura compartilhada entre jobs foi retirada para observar as alterações dos anteriores. |
| P4 | Compactação e envio de lotes com concorrência limitada. | `batch_workers = 2`; cada worker tem staging e relatórios de subprocesso próprios. Confirmar somente após todos os envios necessários. Testar concorrência real e limite configurado. |
| P5 | Reutilização dos resultados de filtros na execução. | Guardar resultados imutáveis de seleções iguais de caminhos históricos; manter matcher do rclone e regras congeladas. Não levar resultados desatualizados a execuções seguintes. |
| P6 | Esforço e threads de compactação por tipo de arquivo. | Níveis/threads separados por tarefa para texto/vídeo/foto/outros, definidos abaixo. Preservar lotes antigos intactos; novos ajustes valem na criação/recriação. |

O cache pressupõe metadados de filesystem confiáveis; leituras completas periódicas continuam necessárias. Workers/threads afetam CPU, memória e espaço temporário. Validar redução de leituras/processos e concorrência limitada, sem prometer ganho fixo em produção sem medir a carga.

## 11. Compressão única e alertas de crescimento — 0.13.0

- `compression_level = 3` (0–9) e `compression_threads = 2` (1–64) para todos os tipos, somente na criação/recriação. Opções anteriores por categoria e max_files saem da configuração.
- `batch_size_alert_percent = 200` alerta a partir do percentual de max_megabytes em bytes originais; 0 desativa. Desconsiderar lotes de um arquivo.
- Agregar alertas e enviar falha Cronitor somente ao final dos jobs, sem interromper e sem mudar retorno operacional 0; não enviar complete depois. Erros operacionais seguem não zero.
- Restauração e rebalanceamento independem da origem. Bootstrap valida índices/graus de recuperação antes de alterar backup; gerações de metadados não substituem retenção/cópias dos dados.

## 12. Documentação bilíngue — 0.6.0

- Manter Markdown, README, requisitos, guias de release/segurança/uso/performance/testes e outros documentos para leitura humana em inglês por padrão, com versões equivalentes em português do Brasil.
- Fornecer links recíprocos entre idiomas e exemplos públicos comentados nos dois idiomas com configurações efetivas equivalentes.
- Manter identificadores de código/configuração, listas de dependências e fixtures de dados estáveis; não traduzi-los em interfaces de máquina diferentes ou cópias de dados.
- Atualizar ambos os idiomas juntos quando requisitos ou comportamento documentado mudarem, incluindo este documento e seus espelhos legados `backuper`.

Aceitação: cada guia mantido deve ter versão correspondente e links recíprocos; os dois idiomas de cada exemplo público devem resultar nas mesmas configurações ao serem lidos.

## 13. Testes, entrega e escopo

- Manter testes unitários dos principais domínios e integração de todas as combinações local/S3 e simples/lotes, incluindo caminhos restaurados, tamanho, SHA-256 e bytes exatos.
- Manter fixtures pequenas versionadas e usar simuladores existentes, como Moto, em vez de serviços AWS/Cronitor reais. Exercitar rclone/7-Zip reais e HTTP localhost onde suportado.
- Cobrir ordenação/reutilização, restauração e garantia de S3 sem downloads, limites de remoção/disco, regressões de segurança, P1–P6 e equivalência entre idiomas/exemplos. A CI deve buscar credenciais e conferir cobertura total incluindo decisões de pelo menos 85%.
- Entregar código-fonte e wheel instalável versionados, com checksums SHA-256. Manter README, exemplos, metadados de release e relatório de testes coerentes com a versão entregue.
- As funcionalidades novas escolhidas são 1 (restauração), 2 (verificação), 3 (proteção de remoções) e 5 (resumo). A proposta 4, criptografia do conteúdo antes do envio, não foi selecionada e fica fora destas alterações. Isso não desativa suporte existente à criptografia de backend/configuração.
- Atualizar estes requisitos consolida o escopo entregue; não altera por si só código/versão do aplicativo nem reescreve artefatos de releases publicadas.

## 14. Verificação por metadados e progresso no terminal — 0.6.1

- Adicionar `[backup].verification_mode = "simple"|"hash"` e `--verification-mode`. O padrão é `"simple"`, inclusive em configurações existentes sem a opção.
- O modo simples compara tamanho em bytes e data de modificação em nanossegundos. Criar o inventário não deve ler conteúdo nem solicitar hashes do destino. Alterações que preservem ambos os atributos podem passar despercebidas.
- O modo hash mantém SHA-256/MD5, cache validado, leituras completas periódicas e erros para hashes necessários ausentes. Trocar de simples para hash exige calcular os hashes ausentes, sem reaproveitar hashes antigos ou vazios. Trocar modos pode reconstruir lotes cujos metadados indexados mudem.
- Manter checksums de lotes/controles e proteções de destino mais novo, retenção, remoções, alterações concorrentes e caminhos. Recuperação confere tamanhos e hashes registrados quando presentes; verificação de cópias segue o modo escolhido. Verificação S3 nunca baixa corpos de objetos.
- Execução interativa exibe etapa, diretório e percentual estimado apenas no stderr do terminal. Inventários usam arquivos/bytes; descoberta e subprocessos opacos podem estimar. Progresso não entra nos logs JSON nem stdout e fica desativado sem terminal.
- Atualizar ambos os READMEs e exemplos; testar precisão/troca de modos, ausência de leitura de conteúdo e separação entre progresso/logs; repetir todos os testes e publicar 0.6.1 no GitHub após sucesso.

## 15. Progresso global, terminal conciso e exclusões — 0.6.2

- Estimar o backup inteiro combinando preflight por job, faixas iguais para jobs e lotes concorrentes ponderados por bytes. Não reiniciar nem voltar o percentual entre etapas/diretórios/tarefas. Só a conclusão bem-sucedida pode exibir 100%.
- Mostrar o subdiretório em trabalho na leitura e nas cópias. Grupos de cópia ficam no mesmo diretório. Omitir eventos por arquivo do terminal interativo, mantendo a auditoria JSON completa.
- Documentar arquivo de exclusões e padrões nativos nos dois READMEs. Incluir o caminho nos exemplos TOML e modelos UTF-8 sem exclusões ativas. Configurar `~/sambinha/sambinha.toml` para `./excludes.txt`, criando o arquivo inicialmente sem regras ativas.
- Testar progresso global crescente, conclusão concorrente fora de ordem, múltiplas tarefas, subdiretórios e separação terminal/auditoria. Repetir todos os testes e publicar a release 0.6.2 com fonte/wheel.

## 16. Retenção de logs e consolidação da release — 0.7.0

- Adicionar `log_retention_days = 14` em `[backup]`, nos exemplos TOML públicos e na configuração real; `0` desativa a limpeza e `--log-retention-days` substitui o valor para uma execução.
- Backups, restaurações e verificações normais removem somente logs `.jsonl` regulares, reconhecidos e inativos do Sambinha, mais antigos que a idade configurada desde a última escrita. Preservar o limite exato, logs atuais/abertos, links, subdiretórios e arquivos de outros tipos. Simulações não removem logs.
- Colocar o percentual global estimado no começo da linha de progresso, seguido da etapa e do subdiretório em trabalho.
- Publicar 0.7.0 incluindo todos os commits posteriores a 0.6.2, metadados/relatórios bilíngues atualizados e fonte/wheel/checksums; confirmar envio de main e da tag ao GitHub.

## 17. Marcos de release solicitados

| Marco | Escopo solicitado |
| --- | --- |
| 0.3.1 | Agrupar por data dentro do diretório e alterar o mínimo de lotes existentes; código, README e testes. |
| 0.4.0 | Funcionalidades 1/2/3/5: restauração, verificação periódica, proteção de remoções e resumos; verificação S3 sem downloads; erros de ocupação acima de 95%. |
| 0.4.1 | Melhorias críticas de segurança. A proposta inicial 0.3.2 foi substituída por esta instrução posterior de versão. |
| 0.5.0 | Todas as melhorias P1–P6, com compactação separada para texto/vídeo/foto/outros. |
| 0.6.0 | Documentação em inglês por padrão com versões pt-BR. A release publicada 0.6.0 contém as alterações acumuladas acima. |
| 0.6.1 | Tamanho/mtime por padrão com hash opcional; progresso exclusivo do terminal, documentação bilíngue e repetição da suíte completa. |
| 0.6.2 | Percentual global do backup, terminal conciso com subdiretórios, exclusões documentadas/configuradas e artefatos da release. |
| 0.7.0 | Retenção configurável de 14 dias de logs e percentual no começo da linha; consolidar e publicar tudo desde 0.6.2. |

Os marcos registram escopo e decisões de versão; não significam que toda versão intermediária foi publicada separadamente. Os detalhes operacionais estão no [guia de uso](usage.pt-BR.md), [guia de performance](performance.pt-BR.md), [guia de S3](s3.pt-BR.md), [segurança](../SECURITY.pt-BR.md) e [README](../README.pt-BR.md).

## 17. Opções de disco, falhas por job e links simbólicos — 0.8.0

`check_source_disk` e `check_destination_disk` são opções por job com padrão true. A segunda também controla a saída da restauração; temporários continuam verificados. `continue_on_job_error` é por job, padrão true, e o job que falhou decide se os seguintes executam. Erros identificam o job, inclusive no preflight. Ignorar sempre links simbólicos da origem/destino, sem seguir, copiar ou traduzir; proteger cópias/históricos existentes enquanto esses caminhos forem ignorados. Manter as proteções de metadados/restauração e detecção de mudanças concorrentes. READMEs resumidos apontam para guias completos bilíngues.

## 18. Jobs sequenciais independentes — 0.9.0

Cada job conclui exclusões, preflight, inventário/cache próprios, transferências/retenção, estado, verificação devida e resumo antes de o próximo começar. O compartilhamento de inventários entre jobs deixa de valer. Em 0.9.0, sobreposições entre destinos de jobs eram permitidas; essa permissão foi substituída em 0.10.0.

## 19. Histórico simples, conflitos e restauração seletiva — 0.10.0

Recusar destinos sobrepostos entre jobs e permitir que o destino de um job anterior seja origem de um posterior. No final de cada job simples, expirar históricos `--backup-dir` pelo maior prazo de exclusão/versão, pois o rclone não identifica por que cada caminho foi deslocado. Preservar arquivos mais novos no destino, informar os caminhos e falhar o job para revisão manual. Permitir que o rclone substitua conflitos de tipo arquivo/diretório, preservando os itens deslocados no histórico. Aceitar seletores `restore --path` repetidos para arquivos/diretórios atuais de jobs simples. Cobrir comportamento local e S3 e executar toda a suíte antes de publicar 0.10.0.

## 20. Atributos do destino e atividade no terminal — 0.10.1

Detectar suporte a atributos POSIX nativos antes das operações de metadados em backups/restaurações locais. Ignorar mudanças de proprietário/permissões em exFAT, FAT e sistemas desconhecidos; preservar conteúdo e datas, metadados POSIX suportados e metadados do S3. A detecção não cria arquivos de teste nem inventários adicionais. Ler contadores estruturados JSON do rclone, incluindo estatísticas de uma linha sem prefixo. Mostrar arquivos conferidos/copiados, remoções, movimentos e tempo decorrido. Durante comparação/exclusões sem total confiável, mostrar atividade sem percentual e retomar o progresso medido quando houver totais de transferência. Manter monotonicidade global e reservar 100% para sucesso. Ajustar a linha à largura do terminal, considerar células Unicode e apagar linhas refluídas no redimensionamento. Cobrir streaming real do rclone, restauração sem POSIX, limpeza em interrupções/timeouts e renderização em terminal real.

## 21. Detalhamento dos logs e saída do rclone — 0.10.2

Escolher níveis de log do arquivo e do terminal independentemente, com erros e resumos por padrão. Solicitar somente saída `ERROR` ao rclone no sync simples, exceto quando alguma saída pedir `detailed`; então solicitar `DEBUG` e transmitir esses registros às saídas detalhadas. Manter estatísticas do progresso interativo sem habilitar outras mensagens do rclone. Preservar arquivos mais novos no destino com `--update`. No rclone 1.60, somente a saída detalhada permite identificar arquivos mais novos ignorados e falhar o job para revisão; o modo padrão de baixo volume não consegue fazer isso. O tratamento de conflitos dos lotes não muda.

## 22. Links conflitantes no destino

Dispensar a varredura dos dados do destino em exFAT/FAT nativo somente quando todas as montagens abaixo do destino também não aceitam links. Sempre validar raízes sem seguir links; sistemas desconhecidos e montagens internas que aceitam links mantêm a varredura.

A regra de links no destino substitui o comportamento do destino na seção 17: trocar links conflitantes nos dados por arquivos/diretórios reais sem seguir ou copiar seus alvos, e ignorar links sem conflito. No sync simples local, examinar entradas do destino somente em diretórios reais compartilhados com a origem e aplicar as exclusões nativas aos conflitos encontrados; não criar inventário completo da origem. Nas escritas batch, preparar caminhos exatos de lotes/controles sem seguir componentes de diretórios e reconstruir lotes/controles indexados que viraram links. Simulações nunca substituem links. Atualizar os dois testes anteriormente adiados de links no destino e retirar seus skips incondicionais; cobrir links quebrados, conflitos arquivo/diretório, alvos intactos, exclusões, links criados após inventário e proteção de raízes de metadados.


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.
