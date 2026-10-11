# Requisitos do Sambinha

[English](specification.md)

Este documento consolida os requisitos originais de backup e todas as alterações de produto solicitadas na conversa até a versão **0.6.1**. O inglês é a versão padrão; esta versão em português do Brasil deve conter requisitos equivalentes. As seções abaixo definem comportamento e critérios de aceitação; os marcos de release ao final permitem rastrear as decisões. A seção de 0.6.1 atualiza os requisitos anteriores que exigiam hashes em todos os modos.

## 1. Plataforma, arquitetura e configuração

- Executar em Linux, incluindo Linux Mint, com Python 3.12 ou mais recente. Os backups não devem exigir privilégios de root.
- Manter código legível, organizado em domínios separados, com comentários explicando comportamentos relevantes. Usar ferramentas Linux existentes, como rclone e um compactador compatível com 7-Zip, quando simplificarem a implementação.
- Manter o projeto no repositório privado `lesantos/sambinha` do GitHub, com README, guias de uso/recuperação, exemplos públicos de configuração, testes e notas de release.
- Validar dependências, origens configuradas, montagens e ocupação dos discos locais antes das transferências. Tarefas independentes devem continuar após uma falha por tarefa; uma falha global de preflight pode interromper a execução antes dos envios.
- Usar configuração TOML com opções globais em `[backup]` e tarefas independentes em `[[jobs]]`. Aceitar arquivo alternativo e sobreposições pela CLI; argumentos omitidos herdam os valores do arquivo.
- Permitir que uma origem tenha várias tarefas, destinos e estratégias independentes. Recusar sobreposição entre origem/destino/estado/logs e entre destinos administrados por tarefas diferentes.
- Resolver caminhos relativos a partir da pasta do TOML e aceitar referências ao diretório pessoal e a variáveis de ambiente.
- Limitar timeouts e fazer uma tentativa inicial de transferência mais, no máximo, três retentativas, com dois segundos entre elas.

## 2. Destinos, estratégias e histórico retido

- Suportar pastas locais, inclusive volumes montados, e Amazon S3. Ambos os destinos devem aceitar as estratégias `simple` e `batch`.
- Copiar somente arquivos ausentes ou diferentes no destino. Usar checksums, sem reduzir a comparação ao tamanho quando um checksum necessário estiver indisponível.
- A cópia simples deve preservar caminhos e atributos/permissões quando suportados. A cópia simples local deve preservar diretórios vazios. S3 não oferece permissões POSIX nativas; metadados originais ficam disponíveis no índice.
- Se o destino tiver um arquivo mais novo, preservá-lo, registrar o conflito e falhar aquela tarefa. Nos lotes, comparar datas originais do índice confirmado, não as datas de upload dos arquivos compactados.
- Reter arquivos apagados na origem por 10 dias e versões anteriores de arquivos editados por 2 dias por padrão, com prazos configuráveis. Limpar histórico em execuções seguintes, inclusive no instante exato do prazo; retenção zero permite limpeza imediata.
- Preservar os bytes históricos da cópia simples e confirmar o registro de aposentadoria antes de substituir ou apagar o arquivo atual no destino. Lotes aposentados devem ficar nas chaves imutáveis até expirar. Um lote com arquivos editados e apagados usa o maior prazo aplicável.
- Lotes reativados não podem ser apagados por registros antigos de aposentadoria. Registrar nova tentativa quando a limpeza de retenção terminar localmente, mas a atualização do índice remoto falhar.
- Usar `GLACIER_IR` como padrão S3, com classes configuráveis. Documentar acesso e características de cobrança no código/exemplos/guias, sem preços fixos. A cópia simples S3 exige uma classe legível sem restore assíncrono.
- Guardar controles JSON e `.sambinha/control.tar.gz` em `STANDARD`, mesmo quando os lotes de dados usam classes mais frias.

## 3. Planejamento, ordenação e reutilização de lotes — 0.3.1

- Agrupar arquivos novos de forma determinística por diretório. Dentro do mesmo diretório, considerar a data original de modificação em nanossegundos, **da mais antiga para a mais recente**, com desempate pelo caminho.
- Dar prioridade aos lotes existentes em vez de reconstruir todo o diretório. Preservar cada lote intacto compatível, sua chave, ordem dos membros e compactação armazenada, inclusive lotes de versões anteriores.
- Minimizar os lotes afetados: reagrupar somente arquivos novos e membros de lotes editados, removidos ou incompatíveis. Não alterar um lote intacto para completar um novo lote ou acomodar um arquivo inserido antes na ordenação.
- Respeitar `max_bytes`, padrão de 1 GiB de bytes originais sem compactação, e `max_files`, padrão de 1000. Um diretório pode ocupar vários lotes; grupos pequenos de diretórios podem compartilhar lote. Cabeçalhos podem fazer o tamanho compactado superar o limite de bytes originais.
- Novos lotes devem separar categorias de compactação preservando a ordem de processamento por data dentro do diretório. Mudanças de categoria podem gerar lotes individuais.
- Preferir pelo menos dois membros quando limites e categoria permitirem. Um lote novo anterior com mais de dois membros pode ceder um arquivo para uma sobra individual nova, desde que ambos continuem válidos e usem a mesma categoria. Arquivos grandes demais ou sem par viável usam lotes individuais.
- Armazenar lotes normais no ancestral comum mais próximo como `batch-<hash>.<format>`. Revisões individuais usam `<directory>/.versions/<hash>/<basename-with-archive-extension>`, evitando colisões e preservando o nome solicitado.
- Preservar a árvore relativa à origem dentro dos arquivos compactados. Aceitar compactadores configuráveis compatíveis com 7-Zip, com `7z` como padrão e formatos `7z`/`zip`.
- Criar controle JSON ao lado de cada lote com caminhos originais, tamanhos, SHA-256, MD5, datas e permissões. Lotes novos enviados também devem registrar tamanho compactado e MD5 para permitir verificação S3 sem baixar o lote.
- Manter espelho local configurável dos controles, sem reter os arquivos compactados de dados. Enviar ao destino um índice de recuperação compactado contendo estado e controles.
- Usar staging temporário e removê-lo após sucesso ou falha. Validar snapshots da origem, integridade do arquivo compactado e caminhos/tamanhos exatos dos membros antes do envio. Confirmar o novo índice somente após enviar com sucesso todos os lotes, controles e índice de recuperação necessários.

Aceitação: inserir um arquivo novo mais antigo deve manter as chaves dos lotes anteriores intactos; editar ou remover um membro não pode reconstruir lotes não afetados. Mudanças de limites/formato só podem substituir lotes incompatíveis. Cada arquivo selecionado deve aparecer exatamente uma vez no índice ativo.

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

## 5. Verificação de integridade — funcionalidade selecionada 2 / 0.4.0

- Disponibilizar `sambinha verify` para checagem imediata e verificações periódicas configuráveis por `verify_interval_days`.
- Na cópia simples local, ler e conferir hashes do destino. Nos lotes locais, conferir checksum do lote quando registrado, testar o arquivo compactado e validar membros extraídos pelos metadados originais.
- No S3, usar **somente metadados dos objetos**, comparando tamanho e MD5 do objeto inteiro com o índice local. Para lotes, conferir tanto os dados compactados quanto os controles JSON.
- **Nunca baixar corpos de objetos S3, lotes, controles JSON ou índices de recuperação como parte da verificação**, inclusive em falhas. Nunca solicitar restore Glacier para concluir uma checagem. Consultas de metadados ainda podem gerar cobrança de API.
- Não tratar ETag multipart como MD5 do objeto inteiro. Usar o checksum da interface de metadados S3 do rclone; checksum necessário ausente deve causar erro explícito.
- Exigir índice local para verificar S3. Lotes antigos sem tamanho/checksum de envio registrado devem falhar explicitamente, sem download alternativo ou reconstrução automática de lotes intactos apenas para estabelecer a referência.
- Registrar a limitação: verificar metadados não descompacta membros nem comprova extração diante de um índice alterado por atacante.
- Manter verificações automáticas desativadas por padrão (`verify_interval_days = 0`) por compatibilidade. Com intervalo positivo, verificar após backups bem-sucedidos quando devido; a primeira execução habilitada verifica imediatamente. A agenda depende das execuções de backup e não instala um timer separado.
- Atualizar `last_verified` apenas após uma verificação periódica bem-sucedida. Falha deve retornar erro, manter a data anterior de sucesso e notificar o Cronitor quando configurado. Verificação manual não atualiza a data periódica. Simulação informa a verificação prevista sem ler conteúdo nem baixar objetos.

Aceitação: executar rclone real contra servidor HTTP que permite HEAD e recusa GET; a verificação S3 deve funcionar por metadados sem requisitar corpos de objetos. Índice local/checksums antigos ausentes, corrupção ou hashes indisponíveis devem falhar sem downloads.

## 6. Proteção contra remoções em massa — funcionalidade selecionada 3 / 0.4.0

- Comparar a seleção atual da origem com o inventário selecionado confirmado anterior, retirando caminhos excluídos pelas regras atuais.
- Bloquear a tarefa antes de alterar histórico, enviar, substituir ou limpar retenção quando a quantidade anterior atingir `deletion_min_files` e a remoção superar `max_delete_percent`.
- Usar como padrão pelo menos 20 arquivos anteriores selecionados e remoção estritamente acima de 50%. Exatamente o limite é permitido; o primeiro backup não tem referência anterior e é permitido.
- Aceitar limites configuráveis e `--allow-mass-deletion` para autorizar remoções intencionais em uma única execução. Não persistir essa autorização na configuração ou no estado da tarefa.
- Registrar caminhos removidos, contagens e limite, retornar falha e notificar o Cronitor quando configurado. Outras tarefas independentes podem continuar.
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
- Preservar `deletion_alert_threshold`, padrão 1000: exceder estritamente esse limite dispara aviso consultivo de exclusões definitivas previstas. Agregar tarefas e contar arquivos/versões originais dentro dos lotes, sem histórico protegido.
- Usar janela de aviso de dois dias, um dia para retenção de um dia e aviso imediato para retenção zero. O aviso não pode interromper backup, mudar prazos ou transformar uma execução bem-sucedida em código de falha.

Aceitação: resumos devem contabilizar corretamente arquivos adicionados/editados/apagados/intactos, reutilização/recriação de lotes e zero bytes transferidos na simulação; avisos existentes de retenção devem continuar consultivos.

## 9. Revisão de segurança e correções críticas — 0.4.1

- Revisar o código segundo orientações de segurança reconhecidas e implementar as correções aplicáveis mais críticas, documentando escopo e validação por regressão.
- Validar esquema de estado/controles, caminhos relativos seguros, metadados/checksums originais, membros ativos únicos e conflitos entre ativos/aposentados antes da retenção ou recuperação. Recusar travessia, caminho raiz, caracteres nulos/quebras de linha e links/arquivos especiais não suportados na origem; reservar `.sambinha` na raiz da origem.
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
| P2 | Transferências simples agrupadas em operações limitadas do rclone. | `transfer_group_size = 100`, `transfer_workers = 4`; usar listas literais, confirmar histórico antes e manter checksums/proteção de destino mais novo/consistência da origem. Conferir menos processos e bytes recuperados exatos. |
| P3 | Inventários equivalentes compartilhados entre tarefas independentes. | Mesma origem, seleção congelada e exigência compatível de leitura completa compartilham um scan na execução, inclusive entre destinos diferentes. Seleções/prazos de leitura diferentes continuam independentes. |
| P4 | Compactação e envio de lotes com concorrência limitada. | `batch_workers = 2`; cada worker tem staging e relatórios de subprocesso próprios. Confirmar somente após todos os envios necessários. Testar concorrência real e limite configurado. |
| P5 | Reutilização dos resultados de filtros na execução. | Guardar resultados imutáveis de seleções iguais de caminhos históricos; manter matcher do rclone e regras congeladas. Não levar resultados desatualizados a execuções seguintes. |
| P6 | Esforço e threads de compactação por tipo de arquivo. | Níveis/threads separados por tarefa para texto/vídeo/foto/outros, definidos abaixo. Preservar lotes antigos intactos; novos ajustes valem na criação/recriação. |

O cache pressupõe metadados de filesystem confiáveis; leituras completas periódicas continuam necessárias. Workers/threads afetam CPU, memória e espaço temporário. Validar redução de leituras/processos e concorrência limitada, sem prometer ganho fixo em produção sem medir a carga.

## 11. Perfis separados para textos, vídeos, fotos e outros

| Categoria | Arquivos típicos | Opção de nível/padrão | Opção de threads/padrão |
| --- | --- | --- | --- |
| Texto | `.md`, `.txt`, texto estruturado/código e README/LICENSE sem extensão | `compression_text_level = 3` | `compression_text_threads = 2` |
| Vídeo | `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm` | `compression_video_level = 0` | `compression_video_threads = 1` |
| Foto | `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.tiff`, `.raw`, `.dng` | `compression_photo_level = 1` | `compression_photo_threads = 1` |
| Outros | Nomes/extensões fora das categorias embutidas | `compression_other_level = 3` | `compression_other_threads = 2` |

- Classificar extensões sem diferenciar maiúsculas e nomes conhecidos de documentação sem extensão; não pressupor identificação pelo conteúdo.
- Aceitar níveis 0–9 e threads 1–64 por lote. Nível 0 empacota sem compactar; níveis maiores aumentam esforço. O uso real de threads depende do formato/algoritmo do compactador.
- Preservar a ordem de processamento por data dentro do diretório. Mudança de categoria inicia outro lote e pode gerar individuais; lotes antigos intactos compatíveis são preservados mesmo quando misturam categorias.
- Documentar o equilíbrio entre tempo de compactação, bytes enviados e custo de armazenamento, especialmente para mídia já compactada. Permitir ajustes independentes de cada categoria.

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

## 15. Marcos de release solicitados

| Marco | Escopo solicitado |
| --- | --- |
| 0.3.1 | Agrupar por data dentro do diretório e alterar o mínimo de lotes existentes; código, README e testes. |
| 0.4.0 | Funcionalidades 1/2/3/5: restauração, verificação periódica, proteção de remoções e resumos; verificação S3 sem downloads; erros de ocupação acima de 95%. |
| 0.4.1 | Melhorias críticas de segurança. A proposta inicial 0.3.2 foi substituída por esta instrução posterior de versão. |
| 0.5.0 | Todas as melhorias P1–P6, com compactação separada para texto/vídeo/foto/outros. |
| 0.6.0 | Documentação em inglês por padrão com versões pt-BR. A release publicada 0.6.0 contém as alterações acumuladas acima. |
| 0.6.1 | Tamanho/mtime por padrão com hash opcional; progresso exclusivo do terminal, documentação bilíngue e repetição da suíte completa. |

Os marcos registram escopo e decisões de versão; não significam que toda versão intermediária foi publicada separadamente. Os detalhes operacionais estão no [guia de uso](usage.pt-BR.md), [guia de performance](performance.pt-BR.md), [guia de S3](s3.pt-BR.md), [segurança](../SECURITY.pt-BR.md) e [README](../README.pt-BR.md).
