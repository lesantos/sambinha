# Lotes persistentes, gerações de recuperação e rebalanceamento manual (0.13.0)

[English](batch-lifecycle.md) · [Configuração](configuration.pt-BR.md)

## Configuração e migração

```toml
[backup]
recovery_index_versions = 3

[[jobs]]
name = "documents-s3"
source = "/mnt/origem/documentos"
destination = "aws:bucket/documentos"
type = "s3"
logic = "batch"
max_megabytes = 1024
compression_level = 3
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

`max_megabytes` limita a soma dos tamanhos originais, antes da compressão, em MB (1 MB = 1.048.576 bytes). O padrão é 1024 MB (1 GiB). Aceita inteiros e decimais positivos e finitos, equivalentes a pelo menos 1 byte; frações de byte são truncadas na conversão interna. Não existe limite de quantidade de arquivos. Todos os tipos usam o mesmo nível (0–9, padrão 3) e threads (1–64, padrão 2). Ajustes de compressão se aplicam aos arquivos compactados criados/recriados, preservando os intactos.

Substitua `max_bytes` por `max_megabytes` nos TOMLs e `--max-bytes` por `--max-megabytes` nos comandos, dividindo o valor antigo por 1.048.576. Por exemplo, `max_bytes = 1073741824` passa a `max_megabytes = 1024`. O nome antigo é rejeitado; os lotes existentes permanecem intactos.

Remova `max_files` e todas as opções `compression_text_*`, `compression_video_*`, `compression_photo_*`, `compression_other_*` dos TOMLs e comandos agendados antigos; são rejeitadas com indicação do parâmetro/linha. Substitua os ajustes por categoria por `compression_level` e `compression_threads`. Controles antigos continuam legíveis e preservam seus membros. A migração não reúne automaticamente os pequenos lotes antigos.

O estado confirmado passa ao schema 2 na primeira publicação 0.13.0. Estados antigos no schema 1 continuam legíveis; executáveis antigos rejeitam o schema novo, evitando que gravem sobre associações/gerações que não entendem. Formatos futuros não suportados não são substituídos silenciosamente por um fallback antigo.

## Formação inicial e arquivos novos

Em cada diretório, ordene juntos arquivos e subpastas pela data de modificação em nanossegundos, dos mais antigos aos mais novos; desempate pelo nome. Ao encontrar uma pasta, percorra toda a subárvore antes do próximo irmão (profundidade primeiro). Arquivos regulares entram nessa ordem. Links da origem são ignorados e exclusões são respeitadas. Usam-se as datas reais das pastas da origem, que podem diferir das pastas recriadas por restore.

Um arquivo com `size * 100 >= int(max_megabytes * 1048576) * individual_batch_percent` recebe um lote exclusivo. O percentual deve ser finito, maior que 0 e no máximo 100; a igualdade já qualifica. O desvio mantém o lote normal aberto: com limite de 100 bytes e arquivos de 40, 200 e 60 bytes, forma-se um lote normal de 100 e um individual de 200 bytes. Arquivos não são divididos. Um lote normal novo só fecha quando o próximo arquivo normal ultrapassaria o limite. Categoria e quantidade de membros não geram fronteiras.

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

## Restauração e perda da máquina

Restore confere o destino e sua montagem, saída e capacidade dos temporários, sem exigir caminho, montagem ou disco da origem. Preserve/recrie configuração do job e credenciais em outra máquina; a identidade registrada precisa coincidir. A saída continua nova/vazia, separada dos caminhos protegidos, com proteção contra links/sobrescrita. A restauração simples usa rclone e não exige índice batch.

O estado ativo fica em `state_dir/JOB/state.json`. Controles JSON ficam em `metadata_dir/JOB` (ou controles antigos no estado) e ao lado de cada lote no destino. Cada geração completa contém estado e controles referenciados ativos/excluídos/retidos.

A publicação conclui lotes alterados e seus controles, grava o journal local durável `publication.json`, envia `.sambinha/indexes/GERAÇÃO.tar.gz` e depois o `.commit.json` com geração, tamanho e SHA-256. Só gerações confirmadas são candidatas à recuperação. Depois atualiza a cópia de compatibilidade `.sambinha/control.tar.gz`, salva atomicamente o estado local e remove o journal. Falha na atualização da compatibilidade pode deixar uma geração nova confirmada recuperável, embora o comando reporte erro. Um próximo backup com journal reconcilia o estado do destino antes de planejar.

Antes de um backup com estado local ausente/inválido, leia o destino e recupere a geração confirmada válida mais recente (ou sua cópia de compatibilidade válida), depois gerações válidas anteriores ou o índice legado. Valide identidade, limites do tar/JSON, unicidade dos caminhos, checksum do marcador, presença dos lotes/controles ativos e tamanhos dos compactados. Não extraia caminhos arbitrários do tar. Metadados de geração usam STANDARD; esses downloads não solicitam restore Glacier.

Destino realmente vazio pode iniciar backup; bucket S3 ausente/inacessível é erro. Um marcador local durável de inicialização permite repetir primeiros envios interrompidos. Destino não vazio sem índice recuperável e sem inicialização reconhecida falha antes de alteração. Links nos metadados e identidades incompatíveis não são aceitos silenciosamente. Origem indisponível nunca equivale a uma origem vazia durante backup.

`recovery_index_versions` guarda uma quantidade positiva de gerações confirmadas (padrão 3); ao limpar gerações antigas, remove a confirmação antes do arquivo da geração. Os dados históricos continuam sujeitos a `deleted_days`/`version_days`: gerações de metadados não são snapshots permanentes independentes. Gerações que apontem para lotes ativos expirados/ausentes são ignoradas. Fallback pode recuperar um ponto de backup anterior. Logs não são necessários para restore. Para perder origem e destino, é preciso outra cópia independente de dados/configuração; metadados não reconstroem conteúdo perdido.

## Verificações e operação

`verify-local-metadata` compara tamanho/data da origem com controles locais; `verify-destination-metadata` compara réplicas de metadados e tamanhos; `verify-destination-integrity` lista/testa os arquivos compactados. Nenhum rebalanceia crescimento automaticamente. Mudar o limite de tamanho não torna, por si só, o lote sujo na verificação local. Acompanhe os alertas finais, teste restores e execute rebalanceamento explicitamente quando desejar.

O produtor valida os mesmos limites do leitor antes de confirmar uma geração: 64 MiB para state.json ou cada controle e 256 MiB para a soma dos controles referenciados. Se exceder, o backup falha antes da publicação do índice, preservando o ponto confirmado anterior. São limites de metadados do formato de recuperação, não um limite de quantidade de membros para empacotamento. Jobs muito grandes podem exigir divisão por origem/configuração ou desativação do cache persistente de fingerprints. No rebalanceamento, exclusões são congeladas e continuam protegendo históricos da expiração, mesmo sem origem.
