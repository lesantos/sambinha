# Performance dos backups

[English](performance.md) · [README](../README.pt-BR.md)

O sync simples local examina entradas do destino em diretórios compartilhados com a origem antes de iniciar o rclone, para substituir links conflitantes com segurança. Isso acrescenta uma passagem pelos diretórios do destino nos sistemas que podem conter links, mas não cria inventário de arquivos da origem nem lê conteúdo; somente links encontrados e diretórios correspondentes exigem consulta aos metadados da origem. Em exFAT/FAT nativo, essa passagem é dispensada se não existir uma montagem interna de sistema que aceite links; permanecem apenas as checagens de raízes e da tabela de montagens. Sistemas desconhecidos usam a varredura. As exclusões nativas do rclone são avaliadas apenas para conflitos de links encontrados. As escritas batch conferem diretamente os caminhos de saída planejados.

P1–P6 foram implementadas na ordem solicitada; 0.9.0 substitui o compartilhamento entre jobs de P3 por inventários independentes. O ganho depende de quantidade de arquivos, discos, CPU e rede. Os testes conferem menos leituras/chamadas de processos e concorrência limitada, sem prometer um ganho fixo.

| Prioridade | Melhoria | Configuração e comportamento |
| --- | --- | --- |
| P1 | Cache de fingerprints batch | Em `verification_mode = "hash"`, `fingerprint_cache = true` reaproveita apenas com dispositivo/inode/tamanho/mtime/ctime/permissões iguais. `fingerprint_full_scan_days = 7` força leitura completa; `--no-fingerprint-cache` desativa. Jobs simples não criam inventário de fingerprints. |
| P2 | Sincronização simples nativa | Jobs simples usam uma operação `rclone sync` para percorrer árvores, aplicar exclusões, atualizar e remover; arquivos substituídos/apagados vão para `--backup-dir`. `transfer_workers = 4` controla a concorrência. Não há inventário recursivo separado do Sambinha nos jobs simples. |
| P2 | Descoberta batch com exclusões | `lsjson` lista um diretório por chamada não recursiva para limitar o tamanho de cada resposta. O Sambinha ainda reúne os resultados em um inventário global antes de planejar os lotes. |
| P3 | Inventários independentes por job (0.9.0) | Cada job inventaria a origem quando começa, com seu próprio cache persistente. A leitura compartilhada entre jobs foi retirada para observar as alterações dos anteriores. |
| P4 | Lotes concorrentes | `batch_workers = 2`; staging separado por worker. A thread principal só confirma o índice depois de todos os uploads. |
| P5 | Cache de filtros | Seleções repetidas de nomes históricos reaproveitam resultados imutáveis na execução; o rclone continua avaliando as regras congeladas. |
| P6 | Compressão única | Um nível e quantidade de threads por job para todos os arquivos, sem fronteiras por categoria. |

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

Cada worker mantém originais e arquivo compactado durante a compactação. Após validar integridade e membros, remova a cópia dos originais antes do envio. Durante o upload, permanecem somente o arquivo compactado e os pequenos arquivos de controle/lista. Os temporários usam o diretório padrão do Python, configurável por `TMPDIR`.

Antes de iniciar os workers, confira o espaço disponível contra uma estimativa conservadora para as maiores `min(batch_workers, lotes pendentes)` necessidades de staging: duas vezes o tamanho real dos originais de cada lote, mais margem de pelo menos 1 MiB por lote (ampliada por quantidade de arquivos e tamanho do controle). Confira novamente cada lote antes de copiar os originais. Use os tamanhos reais porque arquivos individuais e grupos existentes podem exceder `max_megabytes`. As checagens também rejeitam ocupação acima de 95%, mesmo com checagens da origem/destino desativadas. A estimativa não reserva espaço no sistema operacional; escritas externas concorrentes ou sobrecarga incomum do formato/filesystem ainda podem provocar falhas de escrita. Meça tempo total e tamanho enviado antes de aumentar a concorrência.

Jobs simples delegam ao rclone as comparações e não criam inventário de fingerprints do Sambinha. `verification_mode = "simple"` usa a comparação tamanho/data do rclone; `"hash"` passa `--checksum`. `max_delete` define um limite absoluto por job; o rclone aborta se o exceder. Jobs batch continuam usando inventário, cache, modo de verificação e checagem de consistência da origem. A verificação diária batch lê apenas metadados da origem e JSONs locais. O `verify-destination-metadata` mensal baixa apenas metadados, enquanto o `verify-destination-integrity` manual no S3 baixa lotes um por vez sem gravar arquivos extraídos. Lotes e controles mantêm checksums nos dois modos. Veja [checagens dos lotes](batch-checks.pt-BR.md) para uso de disco e agendamento.

O progresso interativo exibe etapa, subdiretório em trabalho e percentual global no stderr, sem entrar nos logs JSON ou stdout. Jobs simples acompanham as estatísticas de transferência reportadas pelo rclone; fases batch usam contadores ponderados e estimativas limitadas. Só a conclusão bem-sucedida exibe 100%. O padrão dos logs JSON e stdout mostra erros e resumos; no sync simples, o rclone só gera mensagens de nível `ERROR`. Configure `log_file_level` ou `log_terminal_level` como `detailed` para solicitar `DEBUG` ao rclone e incluir eventos por arquivo. As estatísticas periódicas do progresso são emitidas separadamente no nível `ERROR`, sem habilitar as demais mensagens. Redirecionar stderr ou executar sem terminal desativa o progresso. Veja os [modos de verificação](../README.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

Referências: [detecção de mudanças do restic](https://restic.readthedocs.io/en/stable/040_backup.html), [concorrência do rclone](https://rclone.org/docs/).


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.
