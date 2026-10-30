# Novidades do Sambinha

## 1.0.0 — 2026-10-30

Primeira versão estável, e o repositório agora é público. Nenhuma mudança de comportamento desde a 0.20.0: as opções, os formatos de estado e índice, o layout do destino e a linha de comando são os que a 0.20.0 entregou, e a partir desta versão seguem versionamento semântico (uma quebra de compatibilidade exige uma nova versão maior). Todo o histórico do Git foi varrido com o gitleaks antes da publicação e nenhum segredo foi encontrado. A suíte completa passa.

## 0.20.0 — 2026-10-30

Facilitar o acompanhamento dos alertas de exclusão e do progresso dos lotes. O `deletion_alert_threshold` agora conta apenas os arquivos realmente removidos da origem: membros de um lote aposentado que continuam na origem (apenas mudaram para um lote reconstruído) deixam de inflar a contagem que dispara o alerta. Quando o alerta dispara, linhas `deletion_candidate` com os arquivos removidos e a data de exclusão definitiva são gravadas no nível de log `summary`, o padrão; os arquivos que só trocaram de lote saem como `deletion_rebatched`, somente em `detailed`, e o `deletion_alert` ganha os totais `removed` e `rebatched`. Em terminal interativo, os uploads de lotes mostram a linha global seguida de uma linha por worker ativo (lote, percentual próprio, etapa e diretório), em vez de todos os workers sobrescreverem uma única linha; o bloco se ajusta à altura do terminal e cada linha à sua largura. Sem terminal, nada muda. A suíte completa passa.

## 0.19.13 — 2026-10-30

Parar de falhar quando um espelho local de controle guarda um tamanho ou md5 de arquivo mais antigo que o do índice commitado. Um job abortava com `Controle local diverge do índice` para um lote de membros idênticos: o arquivo tinha sido reconstruído com outro nível de compressão (a 0.18.0 passou a três níveis) e regravado no destino por uma execução que perdeu o estado, enquanto o espelho, que só é gravado quando falta, manteve o `archive_size` e o `archive_md5` antigos. O espelho é só uma cópia do índice commitado; agora, quando os membros coincidem e só o tamanho ou o md5 do arquivo diferem, o espelho é regravado a partir do índice (menos em simulação) e a execução segue; membros diferentes continuam sendo erro. Nada é lido do destino para isso. O `verify-destination-metadata` continua comparando o índice com o tamanho real do objeto e com o JSON guardado. A suíte completa passa.

## 0.19.12 — 2026-10-29

Informar nos exemplos e nos guias que `fingerprint_cache` e `fingerprint_full_scan_days` só valem com `verification_mode = "hash"`. Os nomes não dizem isso, e no modo `"simple"`, o padrão, elas não têm efeito útil: nenhum conteúdo é calculado nem relido. Nesse modo, `fingerprint_full_scan_days` ainda renova um carimbo de data no estado quando vence, o que causa uma publicação extra ocasional do índice. Os comentários das duas opções em `sambinha.example.toml` e `sambinha.example.pt-BR.toml`, e os trechos de TOML dos guias de configuração e desempenho (em inglês e português), agora dizem isso. Nenhuma mudança de comportamento.

## 0.19.11 — 2026-10-29

Documentar o que um backup lê da S3 e deixar de recomendar uma checagem mensal do destino. Um backup de rotina para a S3 lê um pequeno objeto marcador (a última geração publicada), lista o bucket uma vez para confirmar que cada lote planejado ainda está lá com o tamanho registrado e pede ao destino o checksum que a S3 já guarda de cada lote que envia; nunca baixa os arquivos de lote. O `verify-destination-metadata` custa cerca de duas requisições por lote (uma consulta de metadados e o download do JSON do lote), muitas vezes a listagem de rotina, e só acrescenta que os JSONs de controle remotos coincidem com os locais, já que o backup confirma presença e tamanho. Agora ele é descrito como uma checagem sob demanda (depois de perder ou restaurar o estado local, ao mudar para outra máquina, ou como garantia ocasional) em vez de rotina mensal, nos guias de verificação de lotes, configuração, S3, uso e desempenho (em inglês e português) e no texto de ajuda do comando. O guia de uso em português deixa de dizer que a estratégia batch evita consultas S3 de inventário sem mencionar a listagem única. Nenhuma mudança de comportamento.

## 0.19.10 — 2026-10-29

Documentar todas as opções dos exemplos de configuração e listar só as opções que valem para cada tipo de job. Cada opção, ativa ou comentada, agora tem um comentário acima dizendo o que ela faz (os comentários soltos de `[backup]` que descreviam opções de job foram incorporados às opções a que pertencem), em `sambinha.example.toml` e `sambinha.example.pt-BR.toml`. O job de exemplo simple local não lista mais opções só de batch (compressão, `max_megabytes`, `batch_workers`, `verify_source_changes`, `inventory_from_listing`, ...) e o exemplo batch S3 não lista mais as só de simple ou só de destino local (`transfer_workers`, `max_delete`, `fast_list`, `destination_mount`); as opções de S3 ficam no exemplo simple como comentários, para quando o destino virar S3. Os comentários de `deleted_days` e `version_days` agora dizem como a retenção funciona: em jobs batch o prazo vale por lote, então um lote com um arquivo apagado e outro alterado guarda o maior; jobs simples usam o maior dos dois para tudo. O `test_examples` confere cada job de exemplo contra as opções válidas para o seu tipo, e um novo teste de integração fixa os prazos de retenção de lotes alterados, apagados e mistos. Nenhuma mudança no programa em si.

## 0.19.9 — 2026-10-29

Fazer a verificação periódica da origem seguir as configurações do job. Com `verify_interval_days` definido, um backup que encontra a verificação vencida compara a origem com os controles locais no fim (a ação `verify-local-metadata` faz o mesmo sob demanda). Essa varredura ignorava `verify_source_changes = false` e sempre usava a leitura estrita; assim, em um CIFS cujos números de inode de um diretório diferem entre a listagem e a abertura (mesmo tamanho, datas e modo), ela falhava com `Diretório mudou durante leitura` e fazia um backup completo reportar erro. Agora ela usa o `verify_source_changes` do job e, com `inventory_from_listing = true`, apenas a listagem. Também informa ao `include_paths` quais caminhos estão no inventário atual, para que não sejam checados para links um a um (a economia feita na 0.19.6 para o backup, que essa varredura não tinha recebido: cerca de uma requisição por arquivo em um compartilhamento de rede). Com `verify_source_changes = true` a leitura continua tão estrita quanto antes; um número de inode alterado ainda é reportado como mudança em sistemas de arquivos que não sejam FAT/exFAT. A suíte completa passa.

## 0.19.8 — 2026-10-29

Fazer a linha de progresso acompanhar o inventário e o planejamento. O inventário não tem um total para dividir (a árvore é descoberta enquanto é percorrida), então sua linha mostrava só contadores, e a barra ficava em 19% do fim do inventário até o primeiro envio enquanto os lotes eram planejados. Agora cada inventário deixa a contagem de arquivos em `state/<job>/inventory_count.json` (fora do `state.json` e do índice publicado; um dry-run não grava), e a execução seguinte a usa como estimativa: a linha do inventário mostra uma porcentagem estimada que fica abaixo do fim da etapa até a varredura terminar, e os contadores continuam. Sem contagem anterior (primeira execução, arquivo ausente ou danificado) a linha segue indeterminada, como antes. O planejamento agora nomeia suas etapas na linha (`Conferindo remoções`, `Planejando lotes`, `Lendo o diário de envios`, `Agrupando arquivos em lotes`, `Conferindo lotes no destino`) e move a barra pelo primeiro décimo da etapa de envio. O progresso existe só no terminal, então logs e códigos de saída não mudam; uma chamada custa cerca de 2 µs e a varredura faz poucas por arquivo, então o tempo adicionado é desprezível. Também ordena os imports de um teste da 0.19.7, apontado pelo `ruff check`. A suíte completa passa.

## 0.19.7 — 2026-10-29

Montar o inventário pela listagem da descoberta, sem `stat` por arquivo. Um job batch com `exclude_files` já lista toda a origem com o rclone (tamanho e data de modificação inclusos) e depois percorria a mesma árvore de novo, um `stat` por arquivo, o que em um CIFS levava quase tanto quanto parte da própria listagem. A nova opção de job `inventory_from_listing = true` (desligada por padrão; exige `logic = "batch"`, `verification_mode = "simple"`, `verify_source_changes = false` e `exclude_files`, e é rejeitada caso contrário) usa o tamanho e a data dessa listagem. O resultado tem os mesmos arquivos, na mesma ordem, com os mesmos tamanhos e datas da varredura normal (conferido em cerca de 100 mil arquivos reais e por testes que incluem o caminho de subárvore grande demais), então as chaves dos lotes e o estado salvo não mudam e nada é reenviado. Links, FIFOs, sockets e dispositivos não são listados pelo rclone e nunca entram no backup. A listagem não traz permissões, então esses arquivos são registrados com o modo `0o755`; a nova opção de job `file_mode` (por exemplo `0o644`) define as permissões dos arquivos restaurados e, sem ela, a restauração continua aplicando o modo registrado. Em uma amostra de 8.685 arquivos, descoberta mais inventário levaram cerca de 7 s em vez de cerca de 12 s. A suíte completa passa.

## 0.19.6 — 2026-10-29

Parar de checar links simbólicos de todos os arquivos inventariados uma segunda e uma terceira vez. Logo depois do inventário, o planejador consultava `include_paths` para cada caminho do estado salvo (cerca de 487 mil arquivos) e, para cada um, olhava o caminho no compartilhamento de origem para ver se um link o escondia; os diretórios ficavam em cache, mas o arquivo não, então cada chamada custava uma requisição à rede. Isso rodava duas vezes (planejamento dos lotes e proteção contra remoções), cerca de 6 minutos cada em um CIFS, sem nenhuma mensagem no log e com a barra de progresso parada. Os caminhos que estão no inventário atual agora ficam fora dessa checagem: o inventário não segue nem registra links, então eles não podem estar atrás de um. A checagem continua para todos os outros caminhos (arquivos removidos da origem ou recém-excluídos), que é o caso para o qual ela existe: um diretório trocado por um link não pode fazer seus arquivos antigos parecerem apagados. O resultado de `include_paths` é o mesmo. A suíte completa passa.

## 0.19.5 — 2026-10-29

Corrigir um aviso de lint (`RUF015`) em um teste; o programa não muda. O `ruff check` passa limpo e a suíte completa passa.

## 0.19.4 — 2026-10-29

Fazer o inventário de um job `simple` com `verify_source_changes = false` ler muito menos de um compartilhamento de rede. Para cada arquivo o inventário abria um identificador só de metadados, pedia os atributos mais duas vezes e o caminho mais uma, e então fechava, embora, com as checagens desligadas, nenhuma dessas respostas fosse comparada com nada e a listagem já tivesse fornecido os mesmos atributos. Em um CIFS (cerca de 3 requisições por arquivo, uma thread), um inventário de 487 mil arquivos levava dezenas de minutos sem nenhum evento no log. Nessa combinação o inventário passa a usar os atributos da listagem do diretório e não abre o arquivo; o resultado é idêntico (caminho, tamanho, data, modo), e arquivos que não são regulares continuam recusados com o mesmo erro. `verify_source_changes = true` e o modo `hash` não mudam. O inventário continua sequencial de propósito: em disco rígido ou NAS, leituras em paralelo podem atrapalhar mais do que ajudar. A suíte completa passa.

## 0.19.3 — 2026-10-29

Aumentar os limites de tamanho do índice de recuperação para que um backup grande possa ser publicado, e registrar o tamanho dele. Uma execução de 487 mil arquivos em 4.204 lotes concluiu todos os envios e falhou no último passo com `Índice excede limite de metadados (state.json)`: o índice era recusado quando o `state.json` passava de 64 MiB, então o estado não era confirmado e nada ficava registrado. Os limites agora são 256 MiB para o `state.json` (e para cada controle) e 512 MiB para todos os controles somados; eles continuam existindo de propósito, porque o estado é lido por inteiro (cerca de duas vezes o seu tamanho em memória, mais durante a leitura) e a recuperação mantém todos os controles em memória, então foram dimensionados para uma máquina com alguns GB livres, não removidos. O erro agora informa o tamanho do estado e o limite, e o novo evento `index_size` (nível summary) registra os tamanhos do `state.json`, dos controles e do arquivo publicado a cada publicação concluída, para acompanhar o crescimento antes de chegar a um limite. A suíte completa passa.

## 0.19.2 — 2026-10-28

Nomear o lote cujo arquivo não confere com o inventário e gravar o log da execução no fuso horário da máquina. O erro `Lote incompleto ou caminhos incorretos; envio interrompido` é lançado depois da compactação e antes do envio, então o log da execução não tinha nenhum evento dizendo qual lote falhou nem por quê; agora a mensagem traz a chave do lote, a quantidade de membros encontrados e esperados e até três caminhos ausentes, extras ou com tamanho diferente. O log da execução (o campo `time` de cada evento e o nome do arquivo de log) e as datas `deletion_at` das previsões de exclusão usavam UTC; agora são gravados no fuso local com o respectivo deslocamento (`2026-10-28T12:38:13-03:00`). Leitores de ISO 8601 continuam funcionando. A retenção dos logs usa a data do arquivo, então logs existentes não são afetados; os nomes do histórico no destino e as listagens de arquivos continuam em UTC de propósito. A suíte completa passa.

## 0.19.1 — 2026-10-27

Adotar lotes também a partir dos controles guardados no destino. A 0.19.0 só adotava um lote armazenado quando a réplica local do controle sobrevivia; assim uma execução que perdeu o estado e as réplicas (a pasta de réplicas foi limpa ou nunca foi gravada por uma versão antiga) ainda refazia todo lote que já estava no bucket: recompactava, remedia e comparava com o arquivo armazenado só para descobrir que era idêntico. Agora, sem estado confirmado, um lote planejado sem réplica local utilizável procura o objeto de controle guardado ao lado do arquivo: todos os candidatos cujo arquivo e controle a listagem do destino mostra são trazidos por um único `rclone copy` dos pequenos objetos `.json` (nunca de um arquivo), validados e adotados se coincidem exatamente com os membros planejados e o arquivo continua lá com o tamanho registrado. O controle também é gravado de volta como réplica local. O evento `batches_adopted` ganha `from_destination`. Com um estado confirmado nada muda (só lotes com réplica local são considerados), e `--fresh-start` e rebalanceamentos continuam ignorando tudo. A suíte completa passa.

## 0.19.0 — 2026-10-27

Adotar lotes já armazenados quando o estado se perde. Sem `state.json` (todas as execuções anteriores falharam antes de confirmar, ou a pasta de estado foi apagada) o planejador tratava todos os lotes como pendentes; assim um backup de 4.204 lotes e 4,8 TB recompactava e recomparava a origem inteira mesmo com todos os arquivos já no bucket. Cada réplica local de controle só é gravada depois que o arquivo e o controle foram enviados; por isso agora um lote planejado cuja réplica coincide exatamente com ele (`schema`, `archive`, `files`, com `archive_size` e `archive_md5`) e cujo arquivo continua no destino com esse tamanho (S3: uma listagem do bucket; local: checagem do arquivo) é adotado: sem ler a origem, sem compactar, sem enviar, e entra no próximo estado confirmado. Réplicas ausentes ou ilegíveis, membros alterados e objetos ausentes ou com outro tamanho são refeitos como antes; `--fresh-start` e rebalanceamentos ignoram as réplicas. O novo evento `batches_adopted` (nível summary) informa quantos foram adotados e quantos ainda serão montados. O `state.json` continua sendo gravado só no fim de uma execução bem-sucedida; o journal de envios segue registrando cada lote concluído. Documentado em `docs/batch-lifecycle*.md`; a suíte completa passa.

## 0.18.8 — 2026-10-27

Manter os lotes em paralelo quando um deles é grande demais e escrever os contadores grandes do mesmo jeito em todo lugar. Até agora um único lote pendente acima de `max_megabytes` (um arquivo enorme, ou um grupo que passou do limite) fazia a execução inteira usar um só worker; então uma primeira execução depois de perder o estado, com milhares de lotes pendentes, compactava um por vez enquanto o `batch_workers` ficava parado. Agora os lotes comuns usam os workers configurados, e os grandes, que podem precisar de um disco inteiro de espaço temporário sozinhos, rodam depois, sozinhos e um por vez; arquivos já compactados enviados como estão (zip, 7z...) não usam espaço temporário e nunca serializam nada. A verificação de espaço temporário roda para cada grupo. Na linha de progresso, contadores com cinco ou mais dígitos aparecem como `134.5k` / `2.1M` em qualquer largura de terminal; antes eram abreviados só em terminais estreitos e apareciam como `134500` nos demais.

## 0.18.7 — 2026-10-27

Ler os arquivos recém-enviados uma vez a menos. Além da leitura feita pelo rclone, o Sambinha calculava sozinho o md5 de todo arquivo enviado (gravado no controle do lote para verificação e restauração) antes de enviá-lo: um zip novo de 68 GB era lido três vezes na origem, e um lote `.7z` novo quatro vezes na pasta temporária. No S3 o rclone já grava no objeto o md5 do que enviou (inclusive em envios em várias partes); por isso, depois do upload, o Sambinha pede esse valor com uma requisição de metadados (`lsjson --hash --stat`, nunca o corpo) e dispensa a própria leitura; o novo evento `md5_from_destination` (nível `detailed`) marca cada uso. Se o destino é local, o objeto não tem md5, o tamanho difere do arquivo local ou a requisição falha, ele volta a calcular o hash lendo o arquivo. O arquivo agora é gravado antes de o controle ser montado; o controle continua sendo enviado por último, como antes. A suíte completa passa.

## 0.18.6 — 2026-10-27

Parar de reler arquivos enormes já compactados (zip, 7z...) enviados como estão. Um arquivo assim tem o md5 calculado (gravado no controle do lote para verificação e restauração) e depois é comparado com o destino por `rclone copyto --checksum`; assim, uma execução que perdeu o estado (por exemplo, todas as anteriores falharam antes de confirmar) lia um zip de 68 GB duas vezes pelo CIFS mesmo sem nenhuma mudança. Agora o md5 e o tamanho vêm da réplica local do controle do envio anterior quando caminho, tamanho, data e modo do arquivo continuam idênticos (novo evento `md5_reused` no nível `detailed`); qualquer divergência ou réplica ilegível volta a calcular o hash. No modo de verificação `simple`, esses arquivos são comparados com o destino por tamanho e data (`--ignore-checksum --modify-window 1ns`) em vez de checksum; o modo `hash` e os lotes `.7z` mantêm `--checksum`: um lote só é refeito quando seus membros mudam ou o estado se perde, e então o arquivo novo tem data nova, de modo que só o conteúdo diz se um órfão deixado no destino por uma execução interrompida é o mesmo. `StorageBackend.put` ganha o argumento opcional `metadata_comparison`. A suíte completa (829 testes) passa.

## 0.18.5 — 2026-10-27

Registrar em log cada reinício do servidor rclone privado da descoberta filtrada. Desde a 0.18.1 uma conexão perdida reinicia o servidor e repete a listagem, mas o motivo ficava só em memória, então uma execução que reiniciou várias vezes não deixava rastro do porquê. Cada reinício agora grava um evento `discovery_restart` (visível nos níveis `summary` e `detailed`, não em `error`) com o tipo da exceção, se o rclone ainda estava em execução ou o código de saída, o final do log dele e a contagem de reinícios. Sem outra mudança de comportamento.

## 0.18.4 — 2026-10-27

Corrigir um teste instável. `test_nested_directory_replaced_by_another_directory_is_detected` recriava o arquivo trocado logo após o scan e, em kernels com relógio de sistema de arquivos grosso (Linux 5.15 em ext4), o arquivo novo ficava com o mesmo `mtime_ns` e o mesmo conteúdo, a mudança passava despercebida e o teste falhava na maioria das execuções. Agora ele espera o relógio avançar, como os testes vizinhos. Sem mudança no código do pacote; a suíte completa (823 testes) passa.

## 0.18.3 — 2026-10-27

Permitir que listagens filtradas muito grandes da origem terminem. O servidor rclone privado da descoberta filtrada cortava qualquer resposta ainda em escrita após uma hora (o `--rc-server-write-timeout` padrão dele), então a listagem recursiva de um diretório de primeiro nível enorme em um CIFS lento falhava com `RemoteDisconnected` depois que o rclone já tinha feito o trabalho, e repetir só gastava mais uma hora. O servidor agora sobe com `--rc-server-read-timeout` e `--rc-server-write-timeout` iguais ao `timeout` do job, o mesmo prazo que o Sambinha já aplica a cada requisição.

## 0.18.2 — 2026-10-27

Fazer a suíte de testes passar no macOS; o programa em si não muda. O diretório temporário padrão é resolvido uma vez por sessão de testes (o macOS o coloca em `/var`, um link simbólico que os diretórios de metadados recusam), como o programa já faz na inicialização. Os testes de integração usam `7zz` quando ele está no PATH e `7z` caso contrário, de modo que o `sevenzip` do Homebrew não faz mais cerca de 270 testes serem pulados e a cobertura cair abaixo do piso de 92%. Testes que exigem nomes de arquivo não-UTF-8 são pulados no macOS (o APFS recusa esses nomes), e os testes simulados da tabela de montagem do Darwin não substituem mais o `os.lstat` global, o que os fazia falhar de forma intermitente com cobertura no Python 3.15.

## 0.18.1 — 2026-10-27

Recuperar-se de uma conexão perdida com o servidor rclone privado usado na descoberta filtrada da origem. Um backup falhava com "Inventário filtrado inválido ... BrokenPipeError" porque todo erro de rede com esse servidor era tratado como inventário inválido, e a causa se perdia junto com o log temporário. Agora, se a conexão cair ou o servidor morrer, o Sambinha o reinicia uma vez e repete a listagem (as subárvores já carregadas continuam em memória). Se ainda falhar, o erro diz "Falha de comunicação com o rclone de descoberta", informa a exceção, se o rclone ainda está em execução ou o código de saída, e traz o final do log dele. "Inventário filtrado inválido" fica só para dados malformados. Cada inicialização do servidor grava seu próprio log, então um reinício nunca lê a porta anterior.

## 0.18.0 — 2026-10-26

Comprimir cada tipo de arquivo no seu próprio nível dentro do mesmo arquivo de lote, e publicar tudo o que foi preparado desde a 0.16.0 (a 0.17.0 nunca foi publicada separadamente; a entrada dela, abaixo, vai nesta release). Os arquivos são classificados pela última extensão em três faixas: texto (`compression_text_level`, padrão 1), mídia, arquivos e documentos já comprimidos (`compression_compressed_level`, padrão 0, armazenados como estão) e todo o resto (`compression_other_level`, padrão 1). O lote é montado com um comando do 7-Zip por faixa no mesmo arquivo, primeiro o texto, depois o resto, depois os armazenados, de modo que cada acréscimo copie o mínimo possível; restauração, verificação e a conferência de CRC leem o arquivo pronto exatamente como antes. As listas de extensões são configuráveis (`compression_text_extensions` e `compression_compressed_extensions`, com padrões extensos) e a palavra-chave `"<none>"` representa arquivos sem extensão (texto por padrão; pode ser movida para a outra lista ou omitida para usar a faixa "resto"). Num teste sintético de 117 MB, as três faixas levaram 5,5 s para 62,7 MB, contra 16,3 s para 62,2 MB com o nível uniforme 3 e 3,9 s para 85,5 MB quando todo arquivo que não é texto é armazenado. Mudança incompatível: a opção `compression_level` foi removida e é recusada com uma mensagem que aponta para `compression_other_level` (e as outras duas); arquivos existentes não são afetados, e a checagem de espaço temporário agora considera o pico de acréscimo dos lotes com várias faixas.

## 0.17.0 — 2026-10-26

Backups mais rápidos e uma arquitetura mais limpa. A verificação final da origem não relê mais todos os arquivos no modo hash quando a assinatura stat registrada na varredura ainda confere, e percorre os diretórios por descritores abertos (cerca de 3x menos chamadas `stat`); o hash usa blocos de 1 MiB com md5 numa thread auxiliar, o arquivo gerado recebe fingerprint só com md5, e no modo hash o CRC32 informado pelo 7-Zip é conferido com o calculado no inventário. A descoberta da origem com exclusões pede ao rclone uma listagem recursiva por subárvore de primeiro nível em vez de uma requisição por diretório. Só os caminhos ausentes do inventário atual são reclassificados pelas regras de exclusão, o cache de fingerprints sai do `state.json` e do índice de recuperação publicado para um `fingerprints.json` privado (e deixa de existir no modo simple), os snapshots de estado não copiam mais o índice inteiro, os controles locais são validados uma vez por execução e a poda de índices lista só `.sambinha/indexes`. As novas opções de job `fast_list`, `s3_chunk_size` e `s3_upload_concurrency` ajustam as transferências para S3. Mudanças incompatíveis: a CLI agora usa subcomandos e as opções vão depois da ação (`sambinha restore --job x --output dir`; `sambinha` sozinho e `sambinha backup` continuam fazendo backup, e a ordem antiga falha com uma dica); `sambinha.api` é a fachada pública e `engine`, `recovery` e `config` não reexportam mais nomes internos; `Config` e `Job` são congelados, com slots e só por palavra-chave (`Job` mantém nome, origem e destino posicionais), e os campos de override por job de `Config` foram substituídos por `with_job_overrides`. Internamente: protocolo `StorageBackend` com composição `SimpleOperations` no lugar do mixin, registro uniforme de estratégias (`simple` e `batch`), `storage_factory` injetável, `Context.worker_view` no lugar de `copy.copy`, uma única tabela de opções da CLI e a suíte dividida em `tests/unit` (sem rclone nem 7-Zip) e `tests/integration`, com um `FakeRunner` reutilizável e cerca de 80 testes novos para ramos antes sem cobertura.

## 0.16.0 — 2026-10-26

Executar o mesmo backup a partir de várias máquinas e retomar backups em lotes interrompidos. O job agora é identificado pelo nome (estado schema 3, identidade schema 2) e não mais pelos caminhos absolutos de origem e destino; assim, uma máquina Linux e uma macOS com pontos de montagem diferentes podem fazer backup dos mesmos diretórios para o mesmo destino sem perder o histórico, e os caminhos ficam só como `origins` informativos. O estado criado por versões anteriores migra sozinho na máquina que o criou; um job copiado para outra máquina com caminhos diferentes, ou renomeado, precisa de `--adopt-identity` uma vez. Um marcador `.sambinha/job.json` no destino recusa outro job usando o mesmo prefixo e detecta quando outra máquina publicou um índice mais novo. Backups em lotes interrompidos agora mantêm um journal de uploads (`uploads.json`): a próxima execução reaproveita lotes já enviados que ainda coincidem com a origem, em vez de recompactá-los. Após cada commit o backup relata arquivos, controles e gerações de índice órfãos no destino (evento `orphans`); `--prune-orphans` remove os com mais de um dia, sem tocar em `.sambinha/history` e respeitando o bloqueio de exclusão em massa. `--fresh-start` (ou `resume = "fresh"` no job) descarta o cache de fingerprints, os journals e as sobras, mantendo o estado indexado, de modo que arquivos alterados continuam sendo detectados. Os diretórios temporários passam a carregar o id do processo, e sobras de processos mortos com mais de uma hora são removidas na inicialização. Corrige um teste de cancelamento dependente de tempo.

## 0.15.1 — 2026-10-26

Corrigir sete bugs encontrados em uma auditoria do código e tornar as interrupções limpas. Lotes agora aceitam nomes de arquivo com barra invertida (restauração e verificação de integridade recusavam o lote inteiro), com espaço ou tabulação no final (o 7-Zip os removia do listfile e o job falhava para sempre; os membros agora vão na linha de comando após `--`), e nomes que não são UTF-8 válido falham cedo indicando o arquivo em vez de quebrar com erro de serialização. `verify-destination-integrity` não lança mais AttributeError em lotes passthrough locais, e erros inesperados por job nos comandos de recuperação são registrados com o nome do job. Os espelhos locais de controle de lotes expirados só são removidos após o estado ser gravado, e a publicação tolera espelho ausente, fechando uma janela que podia bloquear um job permanentemente. Lotes passthrough são verificados uma vez em vez de duas. Valores de ambiente parecidos com credenciais com menos de seis caracteres não são mais redigidos das mensagens de erro. SIGTERM agora desmonta como Ctrl+C: o evento final é registrado, a trava e os diretórios temporários são liberados, todo processo filho (rclone, 7-Zip, servidor de descoberta) é encerrado por grupo de processos, os workers de lotes param antes de iniciar uma compressão ou envio cujo resultado seria descartado, e a CLI sai com 143 (130 para SIGINT). Manutenção: constantes e tipos literais compartilhados em `names`, um único `reject_symlinks`, uma `session` comum para o setup de backup e recuperação, `ForeignStateError`/`UnsupportedStateError` tipados em vez de comparação de mensagens, configuração de ruff e mypy, pytest como executor de testes (paralelo com xdist), pre-commit, Dependabot, job de lint e matriz Python 3.12/3.13 no CI, e um Makefile com os comandos de desenvolvimento.

## 0.15.0 — 2026-10-26

Executar também no macOS, além do Linux. Ler a tabela de montagens pelo comando BSD `mount` quando `/proc/self/mountinfo` não existe, classificar APFS e HFS+ como volumes POSIX e `msdos` como FAT, e conferir `source_mount`/`destination_mount` comparando dispositivos com o diretório pai onde o `mountpoint` do util-linux não existe. Usar um descritor somente leitura e não bloqueante no lugar de `O_PATH` nos inventários só de metadados. Resolver o diretório temporário padrão uma vez por execução, pois o macOS o coloca em `/var` (link simbólico para `/private/var`) e os diretórios de trabalho de metadados recusam ancestrais com links. Adicionar `tests/test_macos.py` com testes de plataforma simulada, que rodam em qualquer sistema, e testes reais, que rodam apenas em um Mac, além de um job informativo de macOS no GitHub Actions; os testes reais, incluindo backups simples e em lotes de ponta a ponta, passam no runner `macos-latest`. Documentar instalação pelo Homebrew, pontos de montagem em `/Volumes`, APFS sem distinção de caixa, Acesso Total ao Disco e agendamento por `launchd`. O comportamento no Linux não muda.

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
