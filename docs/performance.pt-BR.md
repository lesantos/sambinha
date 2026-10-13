# Performance dos backups

[English](performance.md) · [README](../README.pt-BR.md)

P1–P6 foram implementadas na ordem solicitada; 0.9.0 substitui o compartilhamento entre jobs de P3 por inventários independentes. O ganho depende de quantidade de arquivos, discos, CPU e rede. Os testes conferem menos leituras/chamadas de processos e concorrência limitada, sem prometer um ganho fixo.

| Prioridade | Melhoria | Configuração e comportamento |
| --- | --- | --- |
| P1 | Cache de fingerprints batch | Em `verification_mode = "hash"`, `fingerprint_cache = true` reaproveita apenas com dispositivo/inode/tamanho/mtime/ctime/permissões iguais. `fingerprint_full_scan_days = 7` força leitura completa; `--no-fingerprint-cache` desativa. Jobs simples não criam inventário de fingerprints. |
| P2 | Sincronização simples nativa | Jobs simples usam uma operação `rclone sync` para percorrer árvores, aplicar exclusões, atualizar e remover; arquivos substituídos/apagados vão para `--backup-dir`. `transfer_workers = 4` controla a concorrência. Não há inventário recursivo separado do Sambinha nos jobs simples. |
| P2 | Descoberta batch com exclusões | `lsjson` lista um diretório por chamada não recursiva para limitar o tamanho de cada resposta. O Sambinha ainda reúne os resultados em um inventário global antes de planejar os lotes. |
| P3 | Inventários independentes por job (0.9.0) | Cada job inventaria a origem quando começa, com seu próprio cache persistente. A leitura compartilhada entre jobs foi retirada para observar as alterações dos anteriores. |
| P4 | Lotes concorrentes | `batch_workers = 2`; staging separado por worker. A thread principal só confirma o índice depois de todos os uploads. |
| P5 | Cache de filtros | Seleções repetidas de nomes históricos reaproveitam resultados imutáveis na execução; o rclone continua avaliando as regras congeladas. |
| P6 | Perfis de compactação | Níveis/threads por tarefa para textos, vídeos, fotos e outros; novos lotes separam categorias. |

## Categorias e padrões

| Categoria | Exemplos | Nível | Threads |
| --- | --- | ---: | ---: |
| Texto | `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.toml`, código e README/LICENSE sem extensão | 3 | 2 |
| Vídeo | `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm` | 0 | 1 |
| Foto | `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.tiff`, `.raw`, `.dng` | 1 | 1 |
| Outros | Extensões fora das categorias embutidas | 3 | 2 |

A classificação ignora maiúsculas nas extensões e reconhece nomes comuns de documentação sem extensão; não identifica formato pelo conteúdo. RAW/TIFF podem se beneficiar de aumentar `compression_photo_level`; vídeos já compactados tendem a se beneficiar de nível 0. Nível 0 não compacta, mas mantém empacotamento/verificação. Níveis 1–9 aumentam esforço; suporte e uso efetivo de threads dependem do formato/algoritmo do 7-Zip. Até 64 threads podem ser configuradas por lote. Lotes antigos mistos usam o perfil de outros quando compactados juntos; lotes intactos mantêm sua configuração.

```toml
[backup]
verification_mode = "simple" # Padrão; use "hash" para comparar o conteúdo.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2

[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
compression_text_level = 3
compression_text_threads = 2
compression_video_level = 0
compression_video_threads = 1
compression_photo_level = 1
compression_photo_threads = 1
compression_other_level = 3
compression_other_threads = 2
```

A ordem por data dentro de cada diretório é preservada. Mudar categoria inicia outro lote; categorias intercaladas podem gerar lotes individuais. Lotes existentes compatíveis mantêm membros/chaves; ajustes valem para novos lotes, sem reenvio forçado de dados intactos.

Concorrência aumenta espaço temporário, CPU e memória. Cada worker usa originais e arquivo compactado; dois podem precisar aproximadamente do dobro do staging de um. Checks de disco falham acima de 95%. Meça tempo total e tamanho enviado antes de aumentar workers/threads; mais concorrência pode prejudicar um disco lento compartilhado.

Jobs simples delegam ao rclone as comparações e não criam inventário de fingerprints do Sambinha. `verification_mode = "simple"` usa a comparação tamanho/data do rclone; `"hash"` passa `--checksum`. `max_delete` define um limite absoluto por job; o rclone aborta se o exceder. Jobs batch continuam usando inventário, cache, modo de verificação e checagem de consistência da origem. Verificação S3 nunca baixa corpos de objetos. Lotes e controles mantêm checksums nos dois modos.

O progresso interativo exibe etapa, subdiretório em trabalho e percentual global no stderr, sem entrar nos logs JSON ou stdout. Jobs simples acompanham as estatísticas de transferência reportadas pelo rclone; fases batch usam contadores ponderados e estimativas limitadas. Só a conclusão bem-sucedida exibe 100%. Eventos por arquivo ficam na auditoria e são ocultados do terminal interativo; resumos/erros permanecem visíveis. Redirecionar stderr ou executar sem terminal desativa o progresso. Veja os [modos de verificação](../README.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

Referências: [detecção de mudanças do restic](https://restic.readthedocs.io/en/stable/040_backup.html), [concorrência do rclone](https://rclone.org/docs/).
