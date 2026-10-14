# Verificação de lotes, cópias dos metadados e testes de integridade

[English](batch-checks.md)

Estas atividades são independentes da criação e da restauração dos lotes e se aplicam a `logic = "batch"`. Em jobs simples, `verify` continua usando `rclone check`.

| Atividade | Frequência sugerida | Leitura |
| --- | --- | --- |
| `sambinha verify --job documents-s3` | Diária | Tamanho/data da origem e JSONs locais |
| `sambinha verify-metadata --job documents-s3` | Mensal | JSONs locais/do destino e metadados do tamanho dos lotes |
| `sambinha integrity-test --job documents-s3` | Manual, quando necessário | Listagem e conteúdo compactado pelo 7-Zip |

## Verificação diária

`verify` compara caminhos selecionados, tamanhos e datas de modificação da origem com os controles locais confirmados. Informa `new_files`, `modified_files`, `removed_files`, `excluded_files`, `batches_to_update`, `new_batches` e `needs_update`. Não lê os arquivos compactados, não calcula MD5/SHA e não consulta o S3. Montagens da origem e exclusões continuam respeitadas; uma origem indisponível gera erro, sem ser tratada como inventário vazio.

Uma checagem concluída retorna 0 mesmo quando há atualizações necessárias; consulte `needs_update` no resumo `verification_complete`. Erros reais retornam código diferente de zero. Execute `backup` para aplicar as atualizações: a verificação não envia nem remove dados. Tamanho/data iguais são considerados suficientes, mesmo se o conteúdo tiver mudado. `verification_mode = "hash"` continua controlando o inventário do backup, não a verificação diária dos lotes.

`verify_interval_days` permite executar essa checagem local depois de backups batch bem-sucedidos quando vencer o prazo. `0` desativa; `1` permite a checagem diária após backups. Não é um agendador em segundo plano. Use seu agendador para executar os três comandos separadamente nas frequências desejadas; o teste de integridade nunca é incluído automaticamente na verificação.

## Diretório configurável dos JSONs locais

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
metadata_dir = "./metadata"
```

O controle de `pasta/batch-abc.7z` fica em `metadata/JOB/pasta/batch-abc.7z.json`. Um catálogo local `.index.json` registra os lotes ativos e a identidade do job, permitindo a checagem diária pelas cópias mesmo sem o estado do backup. Sem `metadata_dir`, ou com valor vazio, o local anterior `state/JOB/controls` continua suportado. O cache deve ficar separado de origens, backups, saídas de restauração e logs.

O estado local já confirmado inicializa um novo diretório de cache sem consultas ao S3. Checagens diárias não regravam controles nem catálogo inalterados. Mudar o diretório também importa controles aposentados disponíveis no local padrão anterior. O backup atualiza os controles quando muda os lotes e remove os controles expirados durante a retenção.

## Publicação e recuperação após falhas

Atualizar um lote envia o arquivo compactado e seu JSON e grava atomicamente o mesmo JSON localmente. Somente depois de concluir todos os envios e as duas cópias dos controles, o backup publica o índice de recuperação e confirma o novo estado/catálogo ativo local. Lotes inalterados não provocam reenvio dos respectivos JSONs remotos.

O disco local e o S3 não participam de uma transação distribuída única. Uma falha retorna erro e mantém a geração confirmada anterior como referência; envios novos incompletos podem ficar sem referência. Repita o backup para concluir a publicação. Gravações locais dos JSONs e do catálogo usam substituição atômica. A verificação mensal aponta cópias ausentes/diferentes e não sobrescreve os JSONs remotos.

## Classes S3 e checagem mensal dos metadados

Cada controle remoto `LOTE.json` e o índice `.sambinha/control.tar.gz` são enviados em **STANDARD**, independentemente da classe configurada para os lotes, inclusive GLACIER ou DEEP_ARCHIVE. Um bucket pode conter objetos em classes diferentes; não é necessário outro bucket. O programa não administra regras de ciclo de vida do bucket: mantenha esses metadados fora das transições para classes sem acesso imediato. Os lotes já recebem a classe escolhida no envio. Veja [classes S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html) e [filtros de ciclo de vida](https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-filters.html).

`verify-metadata` confere os controles conhecidos ativos, excluídos e retidos. Compara todos os valores dos JSONs locais e do destino, sem calcular hashes de conteúdo. Confere também o tamanho compactado registrado por consultas de metadados do lote. Nunca baixa os lotes S3 nem solicita restore Glacier. Baixa apenas os JSONs pequenos; se faltarem tanto o estado quanto o catálogo local, o índice de recuperação com acesso imediato pode inicializar as cópias. Consultas e downloads de metadados podem gerar as cobranças normais de requisições S3.

Isso comprova a concordância dos metadados e a presença/tamanho dos lotes, sem comprovar a integridade do conteúdo compactado. Essa é a atividade separada de integridade. Controles antigos sem tamanho compactado registrado geram erro explícito na checagem de tamanho.

## Integridade manual e precisão das datas

`integrity-test` executa `7z l -slt` para comparar caminhos, tamanhos e datas dos membros e depois `7z t` para testar a integridade do conteúdo compactado. Não compara MD5/SHA nem grava arquivos extraídos. Lotes locais são lidos diretamente no destino. Lotes S3 são baixados e removidos **um por vez**; use `TMPDIR` em um disco montado com espaço suficiente quando o temporário local for pequeno. Lotes GLACIER/DEEP_ARCHIVE precisam de restore prévio pela AWS; o comando não solicita esse restore automaticamente.

O teste pode ler e descomprimir internamente o lote inteiro e consumir CPU/RAM, mesmo sem gravar arquivos extraídos. A origem não precisa existir. Use depois de mover backups, suspeitas de corrupção no armazenamento ou antes de depender de um lote para recuperar dados.

JSONs locais preservam o inteiro `mtime_ns` original. Os formatos compactados podem perder precisão: 7z e ZIP/NTFS preservam **100 ns**, então a fração `123456789` pode virar `123456700`. ZIPs antigos podem registrar apenas segundos inteiros ou datas DOS com resolução de dois segundos. A comparação pela listagem aceita o truncamento na precisão do formato. Essa perda aceita não reduz a precisão da checagem diária contra os metadados originais dos JSONs.

## Espaço na restauração

A restauração batch baixa, extrai e prepara dentro de `--output`, inclusive quando essa pasta é um volume montado. Cada lote compactado e seus membros não selecionados são removidos antes do próximo lote. Arquivos selecionados ficam nesse disco até toda a seleção passar pela validação e são publicados sem sobrescrever. A restauração continua validando os checksums de lotes/arquivos registrados; a verificação leve não remove essas proteções da recuperação.

Reserve no disco de saída o espaço dos arquivos restaurados mais um lote compactado/descompactado. Sistemas sem links físicos podem precisar temporariamente de mais um arquivo durante a publicação. O temporário padrão da máquina não é usado para restaurar os dados dos lotes. Logs e estado continuam nos locais configurados.
