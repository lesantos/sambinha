# Performance dos backups

[English](performance.md) · [README](../README.pt-BR.md)

O sync simples local examina entradas do destino em diretórios compartilhados com a origem antes de iniciar o rclone, para substituir links conflitantes com segurança. Isso acrescenta uma passagem pelos diretórios do destino nos sistemas que podem conter links, mas não cria inventário de arquivos da origem nem lê conteúdo; somente links encontrados e diretórios correspondentes exigem consulta aos metadados da origem. Em exFAT/FAT nativo, essa passagem é dispensada se não existir uma montagem interna de sistema que aceite links; permanecem apenas as checagens de raízes e da tabela de montagens. Sistemas desconhecidos usam a varredura. As exclusões nativas do rclone são avaliadas apenas para conflitos de links encontrados. As escritas batch conferem diretamente os caminhos de saída planejados.

P1–P6 foram implementadas na ordem solicitada; 0.9.0 substitui o compartilhamento entre jobs de P3 por inventários independentes. O ganho depende de quantidade de arquivos, discos, CPU e rede. Os testes conferem menos leituras/chamadas de processos e concorrência limitada, sem prometer um ganho fixo.

| Prioridade | Melhoria | Configuração e comportamento |
| --- | --- | --- |
| P1 | Cache de fingerprints batch | Em `verification_mode = "hash"`, `fingerprint_cache = true` reaproveita apenas com dispositivo/inode/tamanho/mtime/ctime/permissões iguais. `fingerprint_full_scan_days = 7` força leitura completa; `--no-fingerprint-cache` desativa. Jobs simples não criam inventário de fingerprints. |
| P2 | Sincronização simples nativa | Jobs simples usam uma operação `rclone sync` para percorrer árvores, aplicar exclusões, atualizar e remover; arquivos substituídos/apagados vão para `--backup-dir`. `transfer_workers = 4` controla a concorrência. Não há inventário recursivo separado do Sambinha nos jobs simples. |
| P2 | Descoberta batch com exclusões (0.14.0) | Um único `rclone rcd` local autenticado atende listagens não recursivas por HTTP. Filtros nativos conservam a raiz original; sem reiniciar rclone ou criar árvores temporárias por diretório. Cada listagem tem seu próprio prazo `timeout`. O inventário completo ainda precede o planejamento dos lotes. |
| P3 | Inventários independentes por job (0.9.0) | Cada job inventaria a origem quando começa, com seu próprio cache persistente. A leitura compartilhada entre jobs foi retirada para observar as alterações dos anteriores. |
| P4 | Lotes concorrentes | `batch_workers = 2`; staging separado por worker. A thread principal só confirma o índice depois de todos os uploads. |
| P5 | Cache de filtros | Seleções repetidas de nomes históricos reaproveitam resultados imutáveis na execução; o rclone continua avaliando as regras congeladas. |
| P6 | Compressão em três faixas | Níveis por job para texto, outros e já compactados (escolhidos pela extensão) acrescentados a um mesmo arquivo; uma quantidade de threads; fronteiras de lote ignoram tipos. |

## Descoberta da origem com operações limitadas

Jobs batch com `exclude_files` iniciam um único processo RC privado do rclone para a descoberta e o encerram ao concluir. O cliente faz chamadas HTTP autenticadas diretamente ao loopback, sem iniciar um comando `rclone rc` por diretório. O `timeout` global configurado vale separadamente para cada diretório; a inicialização do servidor tem limite igual ao menor valor entre esse timeout e 10 segundos. Falhas, timeout ou interrupção cancelam operações pendentes, encerram/recolhem o servidor e nunca planejam exclusões ou confirmam inventário parcial. A descoberta é sequencial para evitar buscas excessivas em HDs. `batch_workers` e `transfer_workers` não controlam a descoberta.

Filtros nativos de diretórios podem evitar subárvores excluídas quando o rclone consegue inferir essas regras. Padrões regex `{{...}}` desativam essa otimização. Para arquivos Icon do Finder/macOS, use o glob `Icon[\x0d␍]`, que reconhece CR e sua representação visível pelo rclone, preservando nomes como `Icon.png` e `Icon1`. As regras são congeladas por job; edições valem na próxima execução. Nomes históricos continuam sendo avaliados com árvores temporárias, pois os arquivos podem não existir mais.

Uma comparação sintética em 2026-10-15 usou 100 subdiretórios, cada um com um arquivo incluído e um `*.tmp` excluído. A descoberta anterior iniciou 201 processos rclone e levou 92,819 s; a descoberta persistente iniciou um processo e levou 1,700 s, selecionando os mesmos 100 arquivos. Esse pequeno teste local mede somente descoberta, sem estimar compactação, upload ou conclusão de um backup de vários terabytes.

## Inventário batch unificado

Descoberta e inventário agora compartilham um percurso sequencial DFS por data de modificação/nome. Nos jobs com exclusões, cada diretório usa uma chamada síncrona `operations/list`, sem consultas `job/status` ou pausas de acompanhamento. As entradas selecionadas recebem consultas de metadados locais imediatamente, dispensando um segundo percurso recursivo da origem e uma lista global de candidatos. Inventários sem exclusões também analisam os arquivos no primeiro percurso. A verificação diária local de metadados usa o mesmo fluxo.

Links simbólicos na origem são ignorados, incluindo links para arquivos, diretórios, alvos ausentes e ciclos. Diretórios reais permanecem abertos durante sua visita DFS; operações relativas sem seguir links dispensam verificar os ancestrais novamente para cada arquivo e impedem ler conteúdo através de links que substituam entradas. Assinaturas dos arquivos e identidade dos diretórios continuam rejeitando alterações detectadas. O inventário completo continua obrigatório antes do planejamento dos lotes, envios e tratamento de exclusões; a mudança não sobrepõe envios à varredura nem acrescenta workers à descoberta.

O total de arquivos/bytes é desconhecido durante o percurso unificado; por isso o progresso mostra contadores de diretórios/arquivos (e bytes da leitura hash atual), sem percentual de inventário até a conclusão.

Para reproduzir a comparação de leitura antes/depois, preserve uma árvore do pacote anterior à mudança:

```sh
python scripts/benchmark-inventory.py \
  --baseline-root /tmp/sambinha-before \
  --source /caminho/da/amostra \
  --exclude-file /caminho/excludes.txt \
  --output /tmp/inventory-benchmark.json
```

O diretório de referência deve conter o pacote `sambinha/` original. O script aquece cada versão, alterna sua ordem em três repetições medidas e compara todos os metadados ordenados, diretórios selecionados e assinaturas dos fingerprints. Nunca executa backup nem acessa S3. `--mode hash` também lê o conteúdo da amostra; o padrão é `simple`. Os resultados medem apenas o inventário, com caches do filesystem aquecidos e a carga ambiente da máquina; não estimam a velocidade do backup completo.

Uma comparação em 2026-10-15 contra o commit `b9402f7` (Python 3.12.3, rclone v1.60.1-DEV) produziu inventários idênticos em todos os aquecimentos e execuções medidas:

| Amostra | Antes (mediana) | Depois (mediana) | Redução do tempo |
| --- | ---: | ---: | ---: |
| Sintética, 10.000 arquivos selecionados, 421 diretórios listados | 33,984 s | 13,302 s | 60,9% |
| Origem exFAT somente leitura, 604 arquivos selecionados, 199 diretórios listados | 12,152 s | 4,357 s | 64,1% |

Nas repetições sintéticas, as chamadas HTTP caíram de 922–932 para 421; na amostra exFAT, de 546–556 para 199. Foram conferidos os mesmos caminhos ordenados, metadados completos, conjuntos de diretórios selecionados e assinaturas do cache de fingerprints. Outros testes rodavam durante a medição sintética; a medição exFAT ocorreu separadamente. São amostras com caches aquecidos e carga ambiente da máquina; não representam disco frio ou a origem inteira. [Tempos e resultados brutos](benchmarks/inventory-2026-10-15.json).

## Correção de identidade em exFAT/FAT (0.14.3)

O percurso sequencial unificado permanece. O FAT/exFAT nativo pode atribuir outro inode após a saída do objeto do cache do kernel; por isso o inode antigo da descoberta não é uma identidade persistente. Dispositivos nativos reconhecidos permitem somente essa diferença antes da abertura, se todos os demais metadados observados coincidem. Diretórios usam seus descritores abertos como referência para conferir novamente os caminhos. Arquivos permanecem abertos durante a análise e as checagens finais; consultas apenas de metadados usam `O_PATH` e nunca leem conteúdo. O modo hash só reaproveita fingerprints com assinatura exatamente igual à do arquivo aberto; inodes diferentes provocam recálculo de hashes. A tabela de montagens só é consultada para observações iguais nos demais campos e com inodes históricos diferentes; montagens internas usam sua própria política por dispositivo. Não se acrescenta concorrência nem outro percurso recursivo da origem. Os tempos acima são medições históricas da 0.14.2, não uma nova medição da 0.14.3. Veja [checagens de identidade e diagnóstico](usage.pt-BR.md#identidade-da-origem-em-exfatfat).

## Cache de fingerprints entre máquinas

As assinaturas do cache incluem `st_dev` e `st_ino`, então um cache construído em uma máquina nunca coincide em outra (números de dispositivo e inodes diferentes) e também é invalidado quando um disco é remontado com outro número de dispositivo. O primeiro backup em modo hash de um job em uma máquina nova lê todos os arquivos uma vez, exatamente como `--no-fingerprint-cache`; execuções seguintes nessa máquina reutilizam o próprio cache. Quando uma máquina readota o índice remoto porque outra publicou uma geração mais nova, o próprio cache é preservado em vez de substituído pelo do publicador. Nenhum hash obsoleto pode ser reutilizado por uma entrada alheia: assinatura divergente sempre significa novo hash.

## Compressão única e padrões

Os padrões são `compression_text_level = 1`, `compression_other_level = 1`, `compression_compressed_level = 0` e `compression_threads = 2`, configuráveis por job (veja [compressão em três faixas](configuration.pt-BR.md#compressão-em-três-faixas-0180)). Níveis válidos: 0–9; threads: 1–64. O algoritmo e o formato do 7-Zip determinam o uso real de threads. A extensão escolhe a faixa de compressão, nunca as fronteiras dos lotes. Configurações antigas por categoria devem ser substituídas; grupos intactos preservam suas revisões.

```toml
[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
verification_mode = "simple" # Padrão; use "hash" para comparar o conteúdo.
# Estas duas opções só valem com verification_mode = "hash"; no modo "simple" são ignoradas.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2
compression_text_level = 1
compression_other_level = 1
compression_compressed_level = 0
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

A formação inicial usa DFS por data, desviando arquivos individuais sem fechar o lote normal. Grupos confirmados mantêm seus membros mesmo ao crescer; novos arquivos não reabrem grupos antigos. Só rebalanceamento manual redistribui lotes excedidos. Veja [lotes persistentes](batch-lifecycle.pt-BR.md).

Concorrência aumenta espaço temporário, CPU e memória. `batch_workers` limita o ciclo completo de preparação/compactação/envio/limpeza; `compression_threads` controla cada compactador e `transfer_workers` controla transferências rclone no modo simples. Após qualquer falha de lote, interrompa o agendamento dos pendentes e aguarde somente os workers em andamento concluírem e limparem seus temporários. Publique índice/estado somente se todos os envios necessários tiverem sucesso.

Cada worker compacta diretamente da origem; somente o arquivo compactado e pequenos arquivos de controle/lista ocupam o disco temporário. `verify_source_changes = true` confere a consistência da origem antes e depois da compactação, retentando alterações concorrentes até três vezes; `false` ignora essas checagens. A opção é por job, ativada por padrão e aplicável a jobs batch. `[backup].temp_dir`, junto dos outros diretórios configurados, escolhe um diretório temporário existente e gravável compartilhado por todos os jobs; caminhos relativos partem da pasta do TOML. Sem essa opção, vale o diretório temporário padrão do Python (`TMPDIR`).

Antes de iniciar os workers, confira o espaço disponível contra uma estimativa conservadora para as maiores necessidades simultâneas: tamanho dos dados originais de cada lote (estimativa de arquivo compactado incompressível), mais margem de pelo menos 1 MiB por lote (ampliada por quantidade de arquivos e tamanho do controle). Confira novamente cada lote antes de criar o arquivo compactado. Lotes que excedam `max_megabytes` são processados um por vez, sem outros lotes ocupando o disco temporário. Use os tamanhos reais porque arquivos individuais e grupos existentes podem exceder `max_megabytes`. As checagens também rejeitam ocupação acima de 95%, mesmo com checagens da origem/destino desativadas. A estimativa não reserva espaço no sistema operacional; escritas externas concorrentes ou sobrecarga incomum do formato/filesystem ainda podem provocar falhas de escrita. Meça tempo total e tamanho enviado antes de aumentar a concorrência.

Quando faltar espaço, o erro lista os lotes que definiram o pico estimado, o tamanho original de cada lote e até os três maiores arquivos por lote.

Jobs simples delegam ao rclone as comparações e não criam inventário de fingerprints do Sambinha. `verification_mode = "simple"` usa a comparação tamanho/data do rclone; `"hash"` passa `--checksum`. `max_delete` define um limite absoluto por job; o rclone aborta se o exceder. Jobs batch continuam usando inventário, cache, modo de verificação e checagem de consistência da origem. A verificação diária batch lê apenas metadados da origem e JSONs locais. O `verify-destination-metadata`, sob demanda, baixa apenas metadados, enquanto o `verify-destination-integrity` manual no S3 baixa lotes um por vez sem gravar arquivos extraídos. Lotes e controles mantêm checksums nos dois modos. Veja [checagens dos lotes](batch-checks.pt-BR.md) para uso de disco e agendamento.

O progresso interativo exibe etapa, subdiretório em trabalho e percentual global no stderr, sem entrar nos logs JSON ou stdout. Jobs simples acompanham as estatísticas de transferência reportadas pelo rclone; fases batch usam contadores ponderados e estimativas limitadas. Só a conclusão bem-sucedida exibe 100%. O padrão dos logs JSON e stdout mostra erros e resumos; no sync simples, o rclone só gera mensagens de nível `ERROR`. Configure `log_file_level` ou `log_terminal_level` como `detailed` para solicitar `DEBUG` ao rclone e incluir eventos por arquivo. As estatísticas periódicas do progresso são emitidas separadamente no nível `ERROR`, sem habilitar as demais mensagens. Redirecionar stderr ou executar sem terminal desativa o progresso. Veja os [modos de verificação](../README.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

Referências: [detecção de mudanças do restic](https://restic.readthedocs.io/en/stable/040_backup.html), [concorrência do rclone](https://rclone.org/docs/).


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.


## Verificação final, hashing e controles (0.17.0)

Medido com `scripts/benchmark-verify.py` (6000 arquivos pequenos em 400 diretórios mais quatro arquivos de 64 MiB; números brutos em `docs/benchmarks/verify-2026-10-26.json`). O host de medição era compartilhado e sobrecarregado (load average 7-10 em 4 núcleos), então os tempos variaram ±50% entre execuções idênticas; **as contagens de syscalls são exatas, os tempos são apenas indicativos**.

| `verify()` final sobre 6005 arquivos | antes | depois |
| --- | --- | --- |
| chamadas `stat`, modo simples | 42.024 | 12.912 |
| chamadas `stat`, modo hash | 42.024 | 12.912 (re-hash completo) |
| modo hash, cache de fingerprints, sem full scan devido | releitura total (`read` 16.102) | 0 leituras, 1 `stat` por arquivo |
| modo simples, mediana de 5 execuções | 2,86 s | 1,67 s |
| modo hash com cache, mediana de 5 execuções | 5,33 s (cache ignorado) | 1,18 s |

- **Percurso por descritores.** `verify()` abre cada diretório uma vez com `O_NOFOLLOW` relativo ao pai e valida os arquivos com `dir_fd`; a cadeia de `lstat` dos ancestrais por arquivo desapareceu. Diretório trocado por link simbólico ou por outro diretório, e troca durante a checagem do diretório, continuam gerando `BackupError`/`SourceChangedError`.
- **Assinaturas do scan.** Com `verification_mode = "hash"`, `fingerprint_cache = true` e sem full scan devido, `verify()` (passagem final e as checagens em torno de cada execução do 7-Zip) compara a assinatura stat (dev, ino, tamanho, mtime, ctime, modo) com a registrada pelo scan e só relê os arquivos que divergem. `fingerprint_cache = false` e a execução de full scan mantêm o re-hash completo. Uma edição com mesmo tamanho e mtime ainda altera o `ctime` e é detectada.
- **Hashing.** Leituras em blocos de 1 MiB; para arquivos de 4 MiB ou mais o md5 roda em uma thread auxiliar ao lado do sha256 (fila limitada, junção em todos os caminhos de saída, erros relançados). O arquivo gerado é fingerprintado só com md5. No host sobrecarregado o ganho da sobreposição foi de apenas ~12% em teste puro em memória (2,77 s → 2,43 s para 128 MiB) e foi anulado de ponta a ponta pela passagem extra de CRC32 (2,20 s contra 2,41 s para 256 MiB): não houve ganho líquido mensurável aqui; permanece por ser simples e limitado.
- **Conferência de CRC32.** No modo hash o inventário também calcula o CRC32 (só em memória, nunca persistido). Após `7z l -slt`, um membro com CRC diferente gera erro com o caminho; membros sem CRC (arquivos vazios, acertos de cache) são ignorados. Isso fecha a lacuna entre o que o inventário leu e o que o 7-Zip leu, mas só para arquivos hasheados naquela execução.
- **Controles.** Os espelhos locais dos controles são validados uma vez por execução (memoizados por `Context`, invalidados quando um controle é gravado ou um espelho expirado é removido), e não duas; economiza uma leitura e validação de JSON por controle e não foi cronometrado separadamente.

## Descoberta recursiva por subárvore (0.17.0)

Com `exclude_files`, a descoberta fazia um `operations/list` não recursivo por diretório. Agora faz uma chamada não recursiva para a raiz da origem e uma chamada com `recurse: true` por subárvore de primeiro nível, indexando o resultado pelo diretório pai. O percurso não muda: nomes vêm só da listagem, todo `stat`/`open` usa `dir_fd` com `O_NOFOLLOW`, `IsDir` é validado contra o tipo local, a ordem é por data/nome e o rclone aplica as exclusões nativamente. O prazo (`timeout`) vale por chamada, ou seja, por subárvore. Qualquer falha interrompe toda a descoberta e o servidor `rcd` é encerrado.

Custo de memória: o JSON de cada subárvore é lido de forma incremental e só `(caminho, IsDir)` é guardado por entrada (cerca de 150-250 bytes no CPython); 500 mil entradas custam da ordem de 100 MiB até o fim da descoberta. Se uma subárvore passar de 500.000 entradas, ela volta a uma chamada plana por diretório (sem configuração).

Medido com `scripts/benchmark-inventory.py --same-flow` em árvore gerada (441 diretórios, 10.420 arquivos, `*.tmp` e uma subárvore excluídos), rclone local, 5 repetições, inventários idênticos ([dados](benchmarks/discovery-2026-10-26.json)): chamadas `operations/list` 419 -> 20 (1 plana + 19 recursivas); mediana de descoberta + inventário 4,21 s -> 3,70 s (-12%). No loopback local cada chamada custa só ~9 ms; o ganho cresce com a latência por chamada (p. ex. ~30 ms em discos lentos ou montagens de rede) e com o número de diretórios.

## Listagens do destino por execução (0.17.0)

Publicar uma geração de recuperação não lista mais o destino inteiro para podar gerações antigas de `.sambinha/indexes`: `finish()` lista só esse prefixo (`lsjson --include "/.sambinha/indexes/**"`, e o rclone ignora os demais diretórios). O relatório de órfãos agora faz uma listagem completa explícita por execução, reaproveitando a do planejamento (feita antes dos envios da execução) quando o planejamento S3 já listou. Antes, cada `finish()` listava o destino inteiro, até duas vezes por execução.

## Opções do rclone para S3 (0.17.0)

`fast_list`, `s3_chunk_size` e `s3_upload_concurrency` são opções opcionais por job ([configuração](configuration.pt-BR.md)). Só alteram a linha de comando do rclone (apenas jobs S3); por isso não houve benchmark: o efeito depende do tamanho do bucket, dos objetos e da banda, e os padrões não mudam. Lembre que `verification_mode = "hash"` faz o rclone calcular o MD5 de toda a origem a cada sync simples.

## Reclassificação de filtros dos arquivos indexados (0.17.0)

Com `exclude_files`, toda execução montava uma árvore temporária com N arquivos vazios e rodava `rclone lsjson --recursive` para reclassificar *todos* os caminhos indexados (guarda de remoção em massa, previsão/expiração do histórico, detecção de lotes excluídos). Arquivos do inventário atual já foram selecionados pelas mesmas regras congeladas na descoberta, então passam a ser incluídos por construção; só os caminhos fora do inventário atual (removidos, excluídos por uma regra nova, membros de histórico retirado) passam pela árvore. A semântica não muda: um arquivo que saiu do inventário porque uma regra nova o exclui continua sendo classificado como excluído (o lote vai para `state.excluded`), não como removido. O cache por execução continua (chave = conjunto classificado). O `rebalance` não usa o atalho: seu "inventário" é reconstruído dos controles, não descoberto.

Medido com 20.000 caminhos indexados e 3 regras ([dados](benchmarks/state-2026-10-26.json)): execução sem mudanças 5,15 s -> 0,03 s; 10 arquivos removidos 5,41 s -> 0,23 s.

## Cache de hashes fora do `state.json` (0.17.0)

O `state.json` embutia uma entrada em `fingerprints` para cada arquivo, mesmo em `verification_mode = "simple"`, que nunca a consulta. Esse mapa ainda ia dentro do índice de recuperação (limite de 64 MiB, cerca de 200-250 mil arquivos por job), era gravado com `indent=2` e `fsync` a cada salvamento (2-3 por execução), validado entrada por entrada ao carregar e copiado com `deepcopy` duas vezes por execução. Agora:

- O modo simple não persiste fingerprints (um `fingerprints.json` antigo é removido).
- O modo hash guarda o cache em `state/<job>/fingerprints.json` (JSON compacto, gravação atômica, mesmas regras de diretório 0700). É por máquina (`st_dev`/`st_ino`), portanto nunca entra no `state.json` nem no índice publicado. Arquivo ausente, ilegível ou inválido só custa um rehash completo e é regravado; ele só é regravado quando o conteúdo mudou.
- Um `state.json` de versão anterior que ainda embute `fingerprints` carrega normalmente: o cache embutido serve àquela execução, `fingerprints.json` é gravado e o próximo salvamento descarta a cópia embutida (o índice é republicado uma vez sem ela). Índices publicados por versões antigas continuam servindo para recuperação e restauração; o cache embutido neles é de outra máquina e é ignorado. O schema do estado não muda (versões antigas apenas não veem cache e refazem os hashes uma vez).
- `--fresh-start` apaga `fingerprints.json` (não em dry run).
- "Inventário alterado" (que força publicação numa execução sem outras mudanças) agora significa apenas: `last_full_scan` mudou ou há cache embutido legado a migrar. Oscilação dos fingerprints sozinha (por exemplo mudança de `ctime`) regrava `fingerprints.json`, mas não republica.
- Os dois `copy.deepcopy(ctx.state)` viraram um snapshot que copia só o que a execução altera (mapas `active`/`excluded`, entradas de `retired`, `origins`) e compartilha os controles imutáveis.

Medido num estado sintético com 50.000 arquivos indexados ([dados](benchmarks/state-2026-10-26.json)): `state.json` 35,4 MB -> 14,5 MB; um salvamento do `state.json` 2,93 s -> 0,99 s (feito 2-3 vezes por execução); `load_state` 4,9 s -> 2,9 s; cópia do estado por snapshot 0,89 s -> menos de 1 ms. No modo hash o cache custa uma leitura por execução de um `fingerprints.json` de 13,1 MB (cerca de 2,0 s, quase tudo validação) e uma gravação (cerca de 1,6 s) só quando ele mudou. O índice publicado deixa de carregar esses 20,9 MB, então o mesmo job cabe com muito mais arquivos sob o limite de 64 MiB.

## Compressão em três faixas (0.18.0)

Acrescentar a um arquivo 7z existente não recompacta o que já está nele; por isso o lote é compactado como texto, depois outros, depois já compactados, cada um com seu `-mx` ([como](batch-lifecycle.pt-BR.md#como-um-lote-é-compactado-0180)). Uma medição sintética com 7z 23.01 sobre um conjunto misto de 117 MB (texto, executáveis e mídia já compactada; não é um benchmark geral, a sua mistura será diferente):

| Configuração | Tempo | Tamanho do arquivo |
|---|---|---|
| Uniforme `-mx=3` (padrão anterior) | 16,3 s | 62,2 MB |
| Texto `-mx=1`, todo o resto `-mx=0` | 3,9 s | 85,5 MB (executáveis armazenados: ruim) |
| Três faixas: texto 1, outros 1, compactados 0 (novos padrões) | 5,5 s | 62,7 MB |

Os padrões de três faixas levam cerca de um terço do tempo por 0,8% a mais de espaço. Migração: `compression_level` (padrão 3) foi removido na 0.18.0; o sucessor para extensões não listadas é `compression_other_level` (padrão 1). Arquivos existentes não mudam.
