# Lotes persistentes, gerações de recuperação e rebalanceamento manual (0.13.0)

[English](batch-lifecycle.md) · [Configuração](configuration.pt-BR.md)

## Configuração e migração

```toml
[[jobs]]
name = "documents-s3"
source = "/mnt/origem/documentos"
destination = "aws:bucket/documentos"
type = "s3"
logic = "batch"
recovery_index_versions = 3
max_megabytes = 1024
compression_text_level = 1
compression_other_level = 1
compression_compressed_level = 0
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

`max_megabytes` limita a soma dos tamanhos originais, antes da compressão, em MB (1 MB = 1.048.576 bytes). O padrão é 1024 MB (1 GiB). Aceita inteiros e decimais positivos e finitos, equivalentes a pelo menos 1 byte; frações de byte são truncadas na conversão interna. Não existe limite de quantidade de arquivos. A compressão usa três faixas por extensão, cada uma com seu nível (0–9; texto 1, outros 1, já compactados 0) e threads compartilhadas (1–64, padrão 2); veja [compressão em três faixas](configuration.pt-BR.md#compressão-em-três-faixas-0180). Ajustes de compressão se aplicam aos arquivos compactados criados/recriados, preservando os intactos.

Substitua `max_bytes` por `max_megabytes` nos TOMLs e `--max-bytes` por `--max-megabytes` nos comandos, dividindo o valor antigo por 1.048.576. Por exemplo, `max_bytes = 1073741824` passa a `max_megabytes = 1024`. O nome antigo é rejeitado; os lotes existentes permanecem intactos.

Remova `max_files` e todas as opções `compression_text_*`, `compression_video_*`, `compression_photo_*`, `compression_other_*` dos TOMLs e comandos agendados antigos; são rejeitadas com indicação do parâmetro/linha. Substitua os ajustes por categoria por `compression_text_level`, `compression_other_level`, `compression_compressed_level` e `compression_threads` (a 0.18.0 removeu o `compression_level` intermediário; use `compression_other_level`, padrão 1 em vez de 3; arquivos existentes não mudam). Controles antigos continuam legíveis e preservam seus membros. A migração não reúne automaticamente os pequenos lotes antigos.

O estado confirmado passa ao schema 2 na primeira publicação 0.13.0 e ao schema 3 (identidade por nome, abaixo) na primeira publicação após essa mudança. Estados antigos nos schemas 1 e 2 continuam legíveis; executáveis antigos rejeitam o schema novo, evitando que gravem sobre associações/gerações que não entendem. Formatos futuros não suportados não são substituídos silenciosamente por um fallback antigo.

## Como um lote é compactado (0.18.0)

Os membros são classificados por extensão em grupos de texto, outros e já compactados ([opções](configuration.pt-BR.md#compressão-em-três-faixas-0180)). `compress()` executa um `7z a -t<formato> -scsUTF-8 -spd -spf -mx=<nível> -mmt=<threads> <arquivo> -- <membros>` por grupo não vazio, na ordem **texto, outros, já compactados**, sempre sobre o mesmo arquivo: o primeiro comando o cria e cada seguinte acrescenta sem recompactar o que já existe (o 7-Zip copia o fluxo existente; acrescentar 40 MB armazenados a um arquivo de 13 MB levou 0,2 s). Como cada acréscimo copia tudo o que já foi escrito, o grupo menor e mais barato vai primeiro e o grande, armazenado, por último. Dentro do grupo, a ordem dos arquivos do lote é mantida. Um lote cujos arquivos caem num só grupo executa exatamente um comando. Leitura, restauração e verificação não mudam: um arquivo misto (LZMA2, Copy, BCJ+LZMA2) é listado, testado e restaurado como qualquer outro.

O limite de argumentos e o recurso de arquivo de lista (com a regra de espaço final) valem por comando, com um arquivo de lista por grupo. A faixa de progresso 0,1-0,7 é dividida entre os grupos por bytes. A verificação da origem roda antes do primeiro comando e depois do último; a conferência da listagem, o `7z t` e o cruzamento de CRC rodam uma vez, no arquivo final. Se a origem mudar entre comandos, a verificação final falha como antes; uma interrupção entre comandos descarta o diretório temporário e nada é publicado. Lotes passthrough (um único arquivo grande já compactado) não passam pela compressão; o rebalanceamento chama o mesmo `compress()` e também usa as faixas.

Pico de disco: durante um acréscimo o 7-Zip grava o novo arquivo enquanto o antigo existe. Por isso `temporary_bytes()` reserva, em lotes de vários grupos, `2 * bytes_originais - bytes_do_último_grupo` mais a sobrecarga (supondo nenhuma compressão), contra `bytes_originais` mais a sobrecarga com um só grupo. O evento `batch_start`/`would_batch` mantém o nome e ganha `compression_groups` (arquivos, bytes e nível por faixa); `compression_profile` passou a ser `"tiered"`.

## Identidade pelo nome do job

Todo índice (`state.json` local, manifesto de metadados, gerações remotas) registra seu dono. A identidade é `{schema: 2, name, type, logic, namespace}` mais `destination` apenas em jobs S3, onde bucket/prefixo são a mesma string em qualquer máquina. Caminhos de origem e de destino local são informativos: cada gravação atualiza `state["origins"]` com host, plataforma, origem, destino e `last_seen`, limitado aos 8 hosts mais recentes. O mesmo job pode rodar no Linux e no macOS com pontos de montagem diferentes sem perder o histórico.

`state.identity_matches` decide a aceitação. Duas identidades schema 2 precisam ser iguais. Uma identidade antiga (sem `name`) é aceita quando caminhos/tipo/lógica coincidem com o que este job gravaria nesta máquina; o estado é regravado como schema 3 na próxima gravação e `index_pending` republica o índice remoto (evento `identity_migrated`). Qualquer outra diferença é `ForeignStateError`, salvo com `--adopt-identity` (só backup), que também aceita identidade schema 2 diferindo apenas em `name` (job renomeado) e identidade antiga com outros caminhos; a adoção registra `identity_adopted` com as identidades antiga e nova. Tipo, lógica, namespace e destino S3 nunca são adotados.

O destino carrega `.sambinha/job.json` (`schema`, `name`, `type`, `logic`, `created`, `generation`), regravado após cada geração confirmada e nunca em dry run. Antes de inicializar a partir de um destino não vazio, um marcador com outro nome falha cedo: `Destino pertence ao job X; use --adopt-identity para assumir`. Quando existe estado local, a geração do marcador é comparada com a local; uma geração confirmada diferente (outra máquina publicou depois) indica associação local desatualizada e o índice remoto é readotado (evento `remote_generation_differs`), preservando o cache de fingerprints desta máquina. Destinos sem marcador seguem confiáveis como antes. As máquinas fazem backup em alternância, não ao mesmo tempo: a trava de escritor único é por máquina, e a última geração confirmada prevalece.

## Formação inicial e arquivos novos

Em cada diretório, ordene juntos arquivos e subpastas pela data de modificação em nanossegundos, dos mais antigos aos mais novos; desempate pelo nome. Ao encontrar uma pasta, percorra toda a subárvore antes do próximo irmão (profundidade primeiro). Arquivos regulares entram nessa ordem. Links da origem são ignorados e exclusões são respeitadas. Usam-se as datas reais das pastas da origem, que podem diferir das pastas recriadas por restore.

Um arquivo com `size * 100 >= int(max_megabytes * 1048576) * individual_batch_percent` recebe um lote exclusivo. O percentual deve ser finito, maior que 0 e no máximo 100; a igualdade já qualifica. O desvio mantém o lote normal aberto: com limite de 100 bytes e arquivos de 40, 200 e 60 bytes, forma-se um lote normal de 100 e um individual de 200 bytes. Arquivos não são divididos. Um lote normal novo só fecha quando o próximo arquivo normal ultrapassaria o limite. Categoria e quantidade de membros não geram fronteiras.

Se esse arquivo individual já tiver uma extensão de formato compactado reconhecido (por exemplo, ZIP, 7z, RAR, TAR.GZ, GZIP, BZIP2, XZ ou Zstandard), o Sambinha envia o arquivo original sem compactá-lo novamente. A restauração e a verificação de integridade conferem esse arquivo diretamente.

Não se reabrem lotes confirmados apenas para inserir arquivos novos: esses arquivos formam novos grupos pelo mesmo algoritmo, evitando reenvios de conteúdo intacto. A formação inicial respeita fronteiras sequenciais nesse percurso; não procura um empacotamento global que reordene os membros.

## Associação persistente e histórico

Grupos novos registram um `batch_id` lógico estável e o papel `individual` no controle. O nome físico da revisão muda conforme conteúdo/metadados. Arquivos alterados permanecem no grupo original e na mesma posição, mesmo quando tamanho/data mudam. O lote pode crescer além de `max_megabytes`; reduzir o limite não provoca reconstrução automática. Remover um membro mantém os demais no grupo. Um grupo vazio sai do índice ativo, e a revisão anterior segue a retenção de exclusões/versões. Grupos exclusivos continuam exclusivos após encolher. Controles antigos com um arquivo são tratados como individuais.

Cada caminho selecionado aparece em exatamente um controle ativo. Revisões antigas podem conter os mesmos caminhos como histórico; a restauração comum lê apenas a seleção ativa. Exclusões continuam protegendo históricos, inclusive lotes antigos misturando conteúdo incluído/excluído. Trocar o formato do compactado recria as representações afetadas preservando os membros.

Alterar um membro recria/envia todo o lote. Grupos maiores aumentam transferência, temporários e trabalho para restauração parcial, além de concentrar mais conteúdo em um arquivo compactado. Planeje o espaço pelo tamanho real dos grupos; o limite configurado não limita o crescimento de grupos existentes.

## Alertas de crescimento ao final

`batch_size_alert_percent` é um percentual finito não negativo de `max_megabytes`, padrão 200. Zero desativa. Atingir ou ultrapassar o limiar em bytes originais gera alerta para lotes não individuais. Arquivos compactados com um único membro são desconsiderados. Cada lote gera `batch_size_alert` nos logs; as mensagens são agregadas entre jobs.

O alerta não interrompe jobs nem altera o retorno operacional bem-sucedido (0). Depois de concluir todos os jobs permitidos pela política de falhas, envie um único `fail` ao Cronitor com jobs/lotes afetados, sem enviar `complete` em seguida. Erros operacionais continuam retornando código não zero e incluem os alertas no resumo final. Falha na entrega da notificação é erro operacional. Simulações podem registrar alertas previstos, mas não enviam telemetria. Alertas já existentes sobre expiração de históricos mantêm seu comportamento separado, antes da limpeza.

## Rebalanceamento manual

```bash
sambinha rebalance --config sambinha.toml --job documents-s3 --dry-run
sambinha rebalance --config sambinha.toml --job documents-s3
```

Somente jobs batch são aceitos. A operação usa o backup confirmado, sem consultar os dados atuais da origem, e funciona sem a montagem da origem. Todo grupo não individual com mais de um membro e soma acima de `max_megabytes` precisa ser substituído. Todos os grupos conformes e todos os individuais permanecem intactos. Essa é a quantidade mínima possível de lotes ativos existentes que precisam mudar para cumprir o limite. Não reúne pequenos grupos conformes e nunca roda automaticamente.

Agrupe apenas os membros dos lotes afetados, na ordem registrada dos grupos/membros, desvie arquivos individuais pelo percentual configurado e empacote o restante por bytes. Um membro que cresceu além do limite passa a lote exclusivo. Sobras de grupos afetados podem compartilhar novos lotes. O limite não inclui o overhead do compactador. Publicação, histórico e retenção seguem o mesmo fluxo seguro do backup.

Lotes GLACIER/DEEP_ARCHIVE precisam estar disponíveis após restore pela AWS; o rebalanceamento não o solicita automaticamente. Somente os compactados afetados são baixados/extraídos com validação do conteúdo; seus membros são preparados e recomprimidos. Falta de espaço ou erro de disco interrompe a publicação. A simulação informa o planejamento sem extrair lotes, alterar backup/estado ou enviar telemetria. `rebalance_summary` informa quantos lotes foram afetados/preservados. O comando não é uma reorganização completa para reduzir lotes antigos já conformes.

## Interrupção e retomada

Um backup em lotes que morre após alguns envios (rede, Ctrl+C, SIGKILL) não recomeça do zero. Após cada upload bem-sucedido o job acrescenta uma entrada a `state/<job>/uploads.json` (`key`, `batch_id`, `archive_size`, `archive_md5`, metadados dos membros, `uploaded_at`), gravada de forma atômica sob trava porque os workers são threads. A execução seguinte entrega essas entradas ao planejador como associação anterior adicional, de modo que os mesmos membros formam o mesmo lote, e só pula o envio quando o destino ainda lista o objeto com o tamanho do journal **e** todos os membros ainda coincidem exatamente com a origem (caminho, tamanho, mtime, modo e, no modo hash, checksums). Caso contrário a entrada é descartada (`journal_entry_discarded` com `reason` `changed`, `confirmed`, `overlap` ou `missing`) e o lote é refeito. Entradas reaproveitadas registram `journal_reused`. O journal é apagado após o commit que publica o índice de recuperação e salva o estado; simulações o leem, mas nunca o gravam. Ele é só uma dica: arquivo ausente, corrompido ou estranho é ignorado.

## Estado perdido: adoção de lotes já armazenados

Quando o `state.json` não existe ou nunca foi confirmado (todas as execuções anteriores falharam antes do fim, ou a pasta de estado foi apagada), o planejador não conhece nenhum lote como armazenado. Antes de montar um lote planejado, o job procura o controle dele, primeiro na réplica local (`controls/` ou `metadata_dir`, gravada só depois que o arquivo e o controle foram enviados) e, se não houver ou ela for inutilizável, no objeto de controle guardado no destino ao lado do arquivo (um único `rclone copy` em lote dos pequenos objetos `.json`, nunca de um arquivo; a listagem do destino precisa mostrar os dois). Se `schema`, `archive` e `files` do controle coincidem exatamente com o lote planejado, ele registra `archive_size` e `archive_md5`, e o destino ainda tem o arquivo com esse tamanho (a mesma checagem de uma execução comum), o lote é adotado: sem ler a origem, sem compactar, sem enviar; o controle vindo do destino também é gravado de volta como réplica local. Qualquer outro caso (sem controle, controle ilegível ou divergente, membro alterado, objeto ausente ou com outro tamanho) é refeito como antes. O evento `batches_adopted`, no nível `summary`, informa quantos foram adotados (`count`), quantos destes vieram do destino (`from_destination`) e quantos ainda serão montados (`rebuilt`). Com um estado confirmado só são considerados os lotes que têm réplica local; `--fresh-start` e rebalanceamentos ignoram tudo isso. Os lotes adotados entram no próximo estado confirmado como os demais; o `state.json` continua sendo gravado só no fim de uma execução bem-sucedida.

## Relato de órfãos e `--prune-orphans`

Entradas descartadas, interrupções superadas e publicações interrompidas deixam objetos que nenhum estado confirmado referencia. Após um commit bem-sucedido o job calcula, a partir da listagem do destino que já possui (sem nova listagem completa), todo artefato de lote (`batch-<digest>.<ext>`, `.versions/<digest>/<nome>` e seus controles `.json`) não referenciado por `active`, `excluded`, `retired` ou um journal vivo, além de `.sambinha/indexes/<geração>.tar.gz` sem o `.commit.json`. Nada em `.sambinha/history`, o marcador, o índice de compatibilidade e chaves que não tenham forma de artefato de lote são considerados. Havendo ao menos um órfão, o evento `orphans` (nível summary) informa `count`, `bytes`, `oldest_age_days` e até 20 chaves de exemplo; com zero, nada é registrado. As idades vêm de uma listagem restrita às chaves órfãs.

O relato nunca apaga. `--prune-orphans` (só backup) remove órfãos com mais de um dia, registrando `orphan_removed` por chave, e respeita o guard de remoção em massa: mais que `max_delete_percent` dos objetos de lote do job (com pelo menos `deletion_min_files`) registra `mass_deletion_blocked` e falha o job, salvo com `--allow-mass-deletion`. Também roda num backup sem alterações e nunca em simulações.

## Reinício limpo

`--fresh-start` (ou `resume = "fresh"` no job) descarta o cache de fingerprints (`fingerprints.json`) e o último scan completo (forçando novo scan e rehash completos), `uploads.json` e `publication.json`, e registra `fresh_start` listando o que existia. Mantém `active`, `retired` e `excluded`: arquivos alterados continuam sendo detectados pela comparação com os controles confirmados e nada já armazenado é reenviado. A execução também varre, sem limite de idade, temporários deixados por processos mortos.

## Varredura de temporários

Os diretórios de staging se chamam `sambinha-<etapa>-<pid>-<aleatório>`. Após obter a trava de escritor, cada execução remove no diretório temporário efetivo os que tiverem pid inexistente (`ProcessLookupError`; pid que não podemos sinalizar conta como vivo), pertençam ao usuário, sejam diretórios reais (symlinks nunca são seguidos) e tenham sido modificados há mais de uma hora, e registra `temp_cleanup` com `removed` e `bytes`. Simulações não varrem nada.

## Restauração e perda da máquina

Restore confere o destino e sua montagem, saída e capacidade dos temporários, sem exigir caminho, montagem ou disco da origem. Preserve/recrie configuração do job e credenciais em outra máquina; nome, tipo e lógica do job (e destino S3) precisam coincidir; pontos de montagem podem diferir. A saída continua nova/vazia, separada dos caminhos protegidos, com proteção contra links/sobrescrita. A restauração simples usa rclone e não exige índice batch.

O estado ativo fica em `state_dir/JOB/state.json`. Controles JSON ficam em `metadata_dir/JOB` (ou controles antigos no estado) e ao lado de cada lote no destino. Cada geração completa contém estado e controles referenciados ativos/excluídos/retidos.

A publicação conclui lotes alterados e seus controles, grava o journal local durável `publication.json`, envia `.sambinha/indexes/GERAÇÃO.tar.gz` e depois o `.commit.json` com geração, tamanho e SHA-256. Só gerações confirmadas são candidatas à recuperação. Depois atualiza a cópia de compatibilidade `.sambinha/control.tar.gz`, salva atomicamente o estado local e remove o journal. Falha na atualização da compatibilidade pode deixar uma geração nova confirmada recuperável, embora o comando reporte erro. Um próximo backup com journal reconcilia o estado do destino antes de planejar.

Antes de um backup com estado local ausente/inválido, leia o destino e recupere a geração confirmada válida mais recente (ou sua cópia de compatibilidade válida), depois gerações válidas anteriores ou o índice legado. Valide identidade, limites do tar/JSON, unicidade dos caminhos, checksum do marcador, presença dos lotes/controles ativos e tamanhos dos compactados. Não extraia caminhos arbitrários do tar. Metadados de geração usam STANDARD; esses downloads não solicitam restore Glacier.

Destino realmente vazio pode iniciar backup; bucket S3 ausente/inacessível é erro. Um marcador local durável de inicialização permite repetir primeiros envios interrompidos. Destino não vazio sem índice recuperável e sem inicialização reconhecida falha antes de alteração. Links nos metadados e identidades incompatíveis não são aceitos silenciosamente. Origem indisponível nunca equivale a uma origem vazia durante backup.

`recovery_index_versions` guarda uma quantidade positiva de gerações confirmadas (padrão 3); ao limpar gerações antigas, remove a confirmação antes do arquivo da geração. Os dados históricos continuam sujeitos a `deleted_days`/`version_days`: gerações de metadados não são snapshots permanentes independentes. Gerações que apontem para lotes ativos expirados/ausentes são ignoradas. Fallback pode recuperar um ponto de backup anterior. Logs não são necessários para restore. Para perder origem e destino, é preciso outra cópia independente de dados/configuração; metadados não reconstroem conteúdo perdido.

## Verificações e operação

`verify-local-metadata` compara tamanho/data da origem com controles locais; `verify-destination-metadata` compara réplicas de metadados e tamanhos; `verify-destination-integrity` lista/testa os arquivos compactados. Nenhum rebalanceia crescimento automaticamente. Mudar o limite de tamanho não torna, por si só, o lote sujo na verificação local. Acompanhe os alertas finais, teste restores e execute rebalanceamento explicitamente quando desejar.

O produtor valida os mesmos limites do leitor antes de confirmar uma geração: 64 MiB para state.json ou cada controle e 256 MiB para a soma dos controles referenciados. Se exceder, o backup falha antes da publicação do índice, preservando o ponto confirmado anterior. São limites de metadados do formato de recuperação, não um limite de quantidade de membros para empacotamento. Jobs muito grandes podem exigir divisão por origem/configuração ou `verification_mode = "simple"`/`fingerprint_cache = false`; o cache de hashes não faz mais parte do `state.json` nem do índice. No rebalanceamento, exclusões são congeladas e continuam protegendo históricos da expiração, mesmo sem origem.
