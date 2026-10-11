# Performance dos backups

[English](performance.md) · [README](../README.pt-BR.md)

P1–P6 foram implementadas na ordem solicitada. O ganho depende de quantidade de arquivos, discos, CPU e rede. Os testes conferem menos leituras/chamadas de processos e concorrência limitada, sem prometer um ganho fixo.

| Prioridade | Melhoria | Configuração e comportamento |
| --- | --- | --- |
| P1 | Cache de fingerprints | Em `verification_mode = "hash"`, `fingerprint_cache = true` reaproveita apenas com dispositivo/inode/tamanho/mtime/ctime/permissões iguais. `fingerprint_full_scan_days = 7` força leitura completa; `--no-fingerprint-cache` desativa. O padrão `"simple"` usa tamanho/mtime e não calcula hashes do inventário. |
| P2 | Transferência simples agrupada | `transfer_group_size = 100`; listas literais usam um rclone por grupo e `transfer_workers = 4`. Histórico é confirmado antes; validações de origem e destino mais novo permanecem. |
| P3 | Inventário compartilhado | Mesma origem e conteúdo das regras congeladas compartilham uma leitura por execução, inclusive entre destinos diferentes. Seleções/prazos completos diferentes continuam separados. |
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
transfer_group_size = 100
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

O inventário simples padrão consulta tamanho e data de modificação em nanossegundos, sem abrir o conteúdo. Não detecta edições que preservem ambos. O cache de hashes usa metadados do filesystem; não é prova criptográfica de conteúdo intacto. Leituras completas periódicas detectam divergências que o cache pode não perceber. As opções de cache/leitura completa só se aplicam ao modo hash. Arquivos alterados são validados antes/depois dos envios conforme o modo escolhido, e a origem de lotes é conferida antes do commit. Verificação S3 nunca baixa corpos de objetos. Lotes e controles mantêm checksums nos dois modos.

O progresso interativo exibe etapa, diretório e percentual estimado no stderr, sem entrar nos logs JSON ou stdout. O inventário usa quantidade de arquivos ou bytes; a descoberta de diretórios usa a fila conhecida. Subprocessos opacos estimam pelo tempo decorrido e ficam abaixo de 100% até terminar. Redirecionar stderr ou executar sem terminal desativa o progresso. Veja os [modos de verificação](../README.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

Referências: [detecção de mudanças do restic](https://restic.readthedocs.io/en/stable/040_backup.html), [concorrência do rclone](https://rclone.org/docs/).
