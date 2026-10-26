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
| P6 | Compressão única | Um nível e quantidade de threads por job para todos os arquivos, sem fronteiras por categoria. |

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

Todos os arquivos usam `compression_level = 3` e `compression_threads = 2`, configuráveis por job. Níveis válidos: 0–9; threads: 1–64. O algoritmo e o formato do 7-Zip determinam o uso real de threads. Não se classifica por extensão para definir compactação ou fronteiras de lotes. Configurações antigas por categoria devem ser substituídas; grupos intactos preservam suas revisões.

```toml
[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
verification_mode = "simple" # Padrão; use "hash" para comparar o conteúdo.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2
compression_level = 3
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

A formação inicial usa DFS por data, desviando arquivos individuais sem fechar o lote normal. Grupos confirmados mantêm seus membros mesmo ao crescer; novos arquivos não reabrem grupos antigos. Só rebalanceamento manual redistribui lotes excedidos. Veja [lotes persistentes](batch-lifecycle.pt-BR.md).

Concorrência aumenta espaço temporário, CPU e memória. `batch_workers` limita o ciclo completo de preparação/compactação/envio/limpeza; `compression_threads` controla cada compactador e `transfer_workers` controla transferências rclone no modo simples. Após qualquer falha de lote, interrompa o agendamento dos pendentes e aguarde somente os workers em andamento concluírem e limparem seus temporários. Publique índice/estado somente se todos os envios necessários tiverem sucesso.

Cada worker compacta diretamente da origem; somente o arquivo compactado e pequenos arquivos de controle/lista ocupam o disco temporário. `verify_source_changes = true` confere a consistência da origem antes e depois da compactação, retentando alterações concorrentes até três vezes; `false` ignora essas checagens. A opção é por job, ativada por padrão e aplicável a jobs batch. `[backup].temp_dir`, junto dos outros diretórios configurados, escolhe um diretório temporário existente e gravável compartilhado por todos os jobs; caminhos relativos partem da pasta do TOML. Sem essa opção, vale o diretório temporário padrão do Python (`TMPDIR`).

Antes de iniciar os workers, confira o espaço disponível contra uma estimativa conservadora para as maiores necessidades simultâneas: tamanho dos dados originais de cada lote (estimativa de arquivo compactado incompressível), mais margem de pelo menos 1 MiB por lote (ampliada por quantidade de arquivos e tamanho do controle). Confira novamente cada lote antes de criar o arquivo compactado. Lotes que excedam `max_megabytes` são processados um por vez, sem outros lotes ocupando o disco temporário. Use os tamanhos reais porque arquivos individuais e grupos existentes podem exceder `max_megabytes`. As checagens também rejeitam ocupação acima de 95%, mesmo com checagens da origem/destino desativadas. A estimativa não reserva espaço no sistema operacional; escritas externas concorrentes ou sobrecarga incomum do formato/filesystem ainda podem provocar falhas de escrita. Meça tempo total e tamanho enviado antes de aumentar a concorrência.

Quando faltar espaço, o erro lista os lotes que definiram o pico estimado, o tamanho original de cada lote e até os três maiores arquivos por lote.

Jobs simples delegam ao rclone as comparações e não criam inventário de fingerprints do Sambinha. `verification_mode = "simple"` usa a comparação tamanho/data do rclone; `"hash"` passa `--checksum`. `max_delete` define um limite absoluto por job; o rclone aborta se o exceder. Jobs batch continuam usando inventário, cache, modo de verificação e checagem de consistência da origem. A verificação diária batch lê apenas metadados da origem e JSONs locais. O `verify-destination-metadata` mensal baixa apenas metadados, enquanto o `verify-destination-integrity` manual no S3 baixa lotes um por vez sem gravar arquivos extraídos. Lotes e controles mantêm checksums nos dois modos. Veja [checagens dos lotes](batch-checks.pt-BR.md) para uso de disco e agendamento.

O progresso interativo exibe etapa, subdiretório em trabalho e percentual global no stderr, sem entrar nos logs JSON ou stdout. Jobs simples acompanham as estatísticas de transferência reportadas pelo rclone; fases batch usam contadores ponderados e estimativas limitadas. Só a conclusão bem-sucedida exibe 100%. O padrão dos logs JSON e stdout mostra erros e resumos; no sync simples, o rclone só gera mensagens de nível `ERROR`. Configure `log_file_level` ou `log_terminal_level` como `detailed` para solicitar `DEBUG` ao rclone e incluir eventos por arquivo. As estatísticas periódicas do progresso são emitidas separadamente no nível `ERROR`, sem habilitar as demais mensagens. Redirecionar stderr ou executar sem terminal desativa o progresso. Veja os [modos de verificação](../README.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

Referências: [detecção de mudanças do restic](https://restic.readthedocs.io/en/stable/040_backup.html), [concorrência do rclone](https://rclone.org/docs/).


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.
