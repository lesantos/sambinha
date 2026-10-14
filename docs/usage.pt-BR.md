# Ajustando e recuperando seus backups

[English](usage.md)


O [README](../README.pt-BR.md) apresenta a primeira cópia. Este guia detalha os ajustes disponíveis e o caminho para recuperar arquivos. Cada tarefa tem sua própria origem, destino e modo de cópia; escolha nomes que ajudem a distinguir, por exemplo, o disco externo da nuvem.

## Ajustes pela linha de comando

Todos os parâmetros operacionais têm overrides CLI; opções omitidas herdam o TOML:

```bash
.venv/bin/python -m sambinha --job documents-s3 --max-bytes 102400 --max-files 10 --dry-run
.venv/bin/python -m sambinha --job documents-local --deleted-days 30 --version-days 7
.venv/bin/python -m sambinha --config /caminho/outro.toml --job documents-local --destination /mnt/backup
.venv/bin/python -m sambinha --help
```

`--source`, `--destination`, `--type` e `--logic` exigem um único job selecionado. `--no-dry-run` sobrepõe simulação habilitada no TOML. Tipos: `local`, `s3`. Lógicas: `simple`, `batch`. Compactação: `7z` ou `zip`; `compressor` aceita `7z`, `7zz` ou outro executável compatível com a CLI do 7-Zip.

Códigos de saída: **0** sucesso, **1** erro de execução/notificação, **2** configuração inválida, **130** interrupção. Jobs independentes continuam após falha de um job; o resultado global registra falha. Cada job verifica suas origens e dependências somente depois que o anterior termina.

## Regras dos lotes

1. Novos lotes seguem uma ordenação determinística por diretório e, dentro dele, por data de modificação em nanossegundos, da mais antiga para a mais recente; empates seguem o caminho. Arquivos de vários diretórios são acumulados no mesmo lote. Um diretório pode ocupar mais de um lote quando os limites exigirem divisão.
2. `max_bytes` limita a **soma dos tamanhos originais**, antes da compactação; cabeçalhos e dados incompressíveis podem tornar o arquivo compactado maior. `max_files` limita a quantidade de arquivos. Padrões: 1 GiB e 1000 arquivos.
3. Lotes normais ficam no ancestral comum mais próximo dos arquivos, com nome `batch-<hash>.7z`. Os caminhos dentro deles são relativos à raiz da origem, preservando a árvore original.
4. Lotes têm pelo menos dois arquivos quando o conjunto e os limites permitem. Um arquivo maior que o limite, uma origem com um único arquivo ou arquivos que não podem formar um par dentro dos limites são compactados individualmente. Um lote anterior com mais de dois arquivos pode ceder um arquivo para evitar sobra isolada.
5. Arquivos individuais conservam o nome com a extensão substituída, em `<diretório>/.versions/<hash>/<nome>.7z`. Essa revisão evita colisões entre `nome.txt`/`nome.csv` e permite reter versões sem copiar objetos frios.
6. Cada arquivo compactado tem `<nome>.7z.json` ao lado. O espelho em `<state_dir>/<job>/controls/` armazena apenas os controles, sem guardar os lotes compactados.
7. Os lotes são criados em diretórios temporários e apagados inclusive em falhas. Snapshots, teste de integridade do 7-Zip e validação de caminhos/tamanhos do lote precedem o envio. O índice local só é atualizado após enviar os lotes, controles e arquivo de recuperação.
8. Lotes existentes intactos são preservados com sua chave e ordem originais, inclusive lotes feitos antes da versão 0.3.1. Arquivos novos e membros de lotes alterados, removidos ou incompatíveis com os limites/formato atuais são reagrupados pela regra de ordenação. Um lote intacto não cede arquivos para completar um lote novo. Lotes com chave idêntica são reutilizados sem consulta remota; o índice local deve ser preservado e corresponde a um destino administrado exclusivamente pelo job.

## Retenção, recuperação e limites

Backups e restaurações detectam o sistema de arquivos do destino antes de solicitar proprietário e permissões. Em exFAT, FAT e montagens sem suporte POSIX conhecido, o rclone copia o conteúdo e a data de modificação sem `--metadata`; a restauração de lotes também evita `chmod`. Sistemas com atributos POSIX nativos, como ext4 e Btrfs, continuam preservando os metadados. A detecção lê a tabela de montagens, sem criar arquivos de teste; se o suporte não puder ser confirmado, ignora esses atributos. Os metadados armazenados no S3 continuam habilitados.

Na lógica simples, o destino apresenta os caminhos originais. O rclone usa `--backup-dir` em `.sambinha/history/<id-da-execução>` para guardar arquivos substituídos ou apagados; a limpeza expira esses diretórios pelo maior valor entre `deleted_days` e `version_days`, pois o histórico não diferencia os motivos. Na lógica por lotes, arquivos compactados antigos permanecem em chaves imutáveis até expirar; o índice separa `active` e `retired`.

A contagem começa quando uma execução bem-sucedida detecta a alteração/remoção. A limpeza ocorre durante execuções seguintes, inclusive no instante exato da expiração. Retenção zero permite apagar o histórico nessa execução. Um lote com arquivos editados e apagados usa o maior prazo aplicável. Falhas interrompem a operação afetada; preservam o último índice confirmado. Uploads interrompidos podem deixar objetos sem referência; não são apagados automaticamente para evitar perda de dados.

Para recuperar `simple`, copie os arquivos atuais do destino, excluindo `.sambinha/`; consulte o histórico para versões antigas. Para recuperar `batch`:

- Baixe `.sambinha/control.tar.gz` e extraia `state.json` e `controls/` para um diretório separado.
- Consulte as chaves em `state.json.active` e baixe **somente os lotes ativos**; arquivos `retired` contêm versões anteriores.
- Para classes com restore, solicite a restauração na AWS antes do download.
- Extraia com `7z x arquivo.7z -o/pasta/restaurada`. Os caminhos internos já incluem a árvore a partir da raiz da origem. Compare SHA-256 e tamanho com os controles.
- Para recuperar o estado local, reponha `state.json` e `controls/` em `<state_dir>/<job>/`, usando a mesma origem/destino/lógica da identidade no índice.

`batch` confia no espelho local para evitar chamadas S3 de inventário. Exclusões/corrupção feitas externamente no destino precisam ser corrigidas recuperando/reconstruindo o índice; não apague o estado local. Jobs `simple` delegam a comparação ao rclone; o padrão usa tamanho/data e `verification_mode = "hash"` passa `--checksum`. Não há inventário nem cache de fingerprints do Sambinha para esses jobs.

Links simbólicos são ignorados. Arquivos especiais e nomes com quebras de linha são rejeitados com erro explícito. Cada job deve manter sua própria origem/destino separados, e todos devem ficar fora dos diretórios de estado/logs. Destinos entre jobs não podem se sobrepor, mas um destino pode ser origem de um job posterior; veja [as regras de interação](configuration.pt-BR.md#jobs-sequenciais-e-caminhos-sobrepostos). `.sambinha` na raiz da origem é reservado. Atributos locais não exigem troca de proprietário; S3 não oferece permissões POSIX nativas. A simulação cria apenas o log e faz leitura/preflight; não cria lotes/estado, transfere ou apaga arquivos, nem notifica Cronitor.


## Atualizando para a versão 0.2.0

Esta versão usa o comando `sambinha`, o arquivo `sambinha.toml` e a pasta de metadados `.sambinha`. Atualize os comandos de execução e os agendamentos que você tiver criado. A versão anterior continua disponível nas suas cópias já instaladas até que você a remova.

Para preservar backups feitos por uma instalação anterior, use um novo destino e uma nova pasta de estado para o primeiro backup da versão 0.2.0. Mantenha o destino e o estado anteriores juntos para consultar ou restaurar esse histórico. O programa recusa reutilizar um estado de outro formato de metadados; ele não converte arquivos remotos automaticamente. Esta mudança não exige reenviar dados para testar: comece sempre com `--dry-run`.

## Arquivos mais novos no destino

Em jobs simples, o rclone aplica `--update`: arquivos mais novos no destino não são substituídos por versões mais antigas da origem. Por padrão, o Sambinha pede somente erros ao rclone; no rclone 1.60, a identificação de cada arquivo preservado só aparece em `DEBUG`, então essa situação não falha o job no modo padrão. Configure `log_file_level = "detailed"` ou `log_terminal_level = "detailed"` para receber os caminhos e falhar o job para revisão manual. Os demais critérios de comparação seguem o backend e as opções de comparação configuradas no rclone.

Nos lotes, a comparação considera as datas originais dos arquivos no último índice confirmado. A data de upload dos lotes não é a data de edição dos documentos. Conflitos aparecem como `newer_destination` nos logs, e a execução termina com código 1 e um evento Cronitor `fail` com a descrição dos arquivos. A simulação registra o conflito e retorna erro, sem enviar notificações.

Se a execução simples encontrar falha, inclusive por `max_delete`, o rclone pode já ter concluído outras transferências ou preservado itens em `--backup-dir`; examine o log e o destino antes de repetir.

## Exclusões por arquivo de regras

Configure `exclude_files` por tarefa, ou use `--exclude-files CAMINHO`. Caminhos relativos partem da pasta do TOML e podem usar variáveis carregadas de secrets. Um arquivo ausente, ilegível ou com padrões inválidos faz a execução falhar antes do backup, com erro no Cronitor.

```toml
[[jobs]]
name = "documentos"
source = "~/Documentos"
destination = "/mnt/backup/documentos"
exclude_files = "excludes.txt"
```

```text
# Conteúdo de excludes.txt
*.tmp
*.{bak,old}
/cache/**
```

Jobs simples passam `--exclude-from` ao `rclone sync`, que aplica as exclusões durante sua própria travessia. Por padrão, caminhos excluídos não são removidos do destino; não habilitamos `--delete-excluded`. Jobs batch usam a mesma sintaxe nativa do rclone para selecionar arquivos.

Arquivos excluídos já existentes no destino simples são preservados. Históricos que contenham algum arquivo excluído também são preservados. Um lote ativo antigo com membros excluídos vai para `state.json.excluded`, mantendo seu arquivo e controle no destino e na cópia de recuperação; não é um lote atual da seleção filtrada. Isso evita apagar parte de um lote que não pode ser dividida sem restauração.

Remover uma regra volta a incluir esses caminhos nas próximas execuções. Históricos já vencidos podem ser removidos ao voltar à seleção; seus prazos originais não são reiniciados. A simulação mostra somente arquivos selecionados e não altera estado ou destino. Sem `exclude_files`, o comportamento anterior permanece.

As regras são copiadas para um arquivo temporário no início de cada job, mantendo a mesma seleção na origem, no destino e no histórico daquele job. Uma alteração nas regras de um job posterior será observada quando ele começar.

## Alerta antecipado de exclusões definitivas

```toml
[backup]
deletion_alert_threshold = 1000
```

A opção equivalente na linha de comando é `--deletion-alert-threshold`. O limite é estrito: exatamente 1000 não dispara; mais de 1000 dispara. A contagem é independente por job batch e conta os arquivos/versões dentro dos lotes, não apenas a quantidade de arquivos compactados. O histórico simples de `--backup-dir` não é contado nem limpo pelo Sambinha.

Cada histórico novo registra a data de criação, a retenção, os caminhos originais, o vencimento e o início do aviso. O período é de dois dias para retenções de dois ou mais dias, um dia para retenção de um dia e zero para retenção imediata. O programa precisa ser executado regularmente para avisar na janela: ele não envia eventos enquanto estiver parado e não adia exclusões para compensar uma execução perdida.

Quando o limite é ultrapassado, os eventos `deletion_alert` e `deletion_candidate` listam os arquivos e as datas. `deletion_scheduled` registra o planejamento quando o histórico é criado; `expire` registra a remoção efetiva. Históricos protegidos por exclusões não entram na contagem. O Cronitor recebe `fail` com o resumo, mas o código de saída continua 0 e `end.success` continua verdadeiro se não houver falha real. Não é necessário confirmar a exclusão: ela ocorre no prazo que já estava configurado. O aviso pode repetir nas execuções da janela, até a limpeza terminar.

Em jobs simples, arquivos apagados da árvore atual vão para o histórico nativo do rclone, sem prazo automático de retenção. O aviso e a limpeza por dias tratam apenas do histórico batch.

## Verificação dos discos montados

A lógica corresponde a:

```bash
mountpoint -q "$ponto" &&
[ -n "$(find "$ponto" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]
```

O programa usa `mountpoint -q` e verifica a primeira entrada do diretório em Python, sem percorrer seu conteúdo inteiro. Informe os pontos esperados por tarefa:

```toml
[[jobs]]
name = "discos"
source = "/mnt/dados/documentos"
destination = "/mnt/capsule/LLLD/linux/documentos"
source_mount = "/mnt/dados"
destination_mount = "/mnt/capsule/LLLD"
```

O diretório copiado deve estar dentro do ponto informado. Subpastas podem estar vazias ou ser criadas no primeiro backup, desde que o volume esperado esteja montado e não vazio. Isso corresponde ao exemplo que verifica `/mnt/capsule/LLLD` antes de copiar para sua subpasta `linux`. Para descobrir o ponto de uma pasta existente, use `findmnt -n -o TARGET --target /caminho/da/pasta`.

As opções aceitam `~`, variáveis de secrets e caminhos relativos ao TOML. Também podem ser sobrepostas com `--source-mount` e `--destination-mount`; uma string vazia desativa aquela conferência na execução. Sem esses parâmetros, continuam as verificações normais de diretórios, mas não é possível detectar que um disco específico ficou desmontado. `destination_mount` não se aplica ao S3.

A montagem é validada antes de qualquer transferência e novamente antes de operações de cópia, preservação e exclusão. Uma falha retorna código 1, registra o ponto envolvido e envia Cronitor `fail` com a descrição; nada é escrito no diretório que ficaria no lugar de um volume desmontado. A simulação faz a mesma leitura de montagem, sem notificar. É necessário que o utilitário Linux `mountpoint` esteja instalado, sem exigir root para as verificações.

## Restauração por comando e verificação agendada

```bash
sambinha restore --job documents --path dir/file.txt --output /mnt/recovered/file
sambinha restore --job documents --path dir --path other.txt --output /mnt/recovered/selection
sambinha restore --job documents --output /mnt/recovered/all
sambinha restore --job documents --path dir --output /mnt/recovered/preview --dry-run
sambinha verify --job documents
sambinha --verify-interval-days 7
```

Selecione uma tarefa e uma saída nova/vazia, separada de origens, destinos, estado e logs. Os caminhos são literais, relativos ao índice ativo; a árvore original é preservada. Todos os arquivos selecionados têm tamanho e hashes SHA-256/MD5 registrados no índice, quando presentes, conferidos em área temporária antes da publicação. Arquivos são criados sem sobrescrever os existentes; permissões privilegiadas não são aplicadas. Links e caminhos inseguros nos lotes são recusados antes da extração. Uma falha posterior de disco/escrita pode deixar arquivos já publicados e validados, com retorno de erro.

Jobs simples restauram a árvore atual sem índice e aceitam `--path` para arquivos/diretórios atuais. Para uma versão histórica simples, use rclone no diretório `.sambinha/history/<id-da-execução>`. Lotes podem recuperar `state.json` do tar remoto, lendo apenas o membro regular limitado a 64 MiB, sem extrair outros caminhos ou instalar um estado novo. Restaurar do S3 baixa os arquivos/lotes selecionados e pode gerar cobrança. GLACIER/DEEP_ARCHIVE exigem restore prévio na AWS.

Jobs simples executam `rclone check`. Lotes locais validam checksum do arquivo compactado, integridade do 7-Zip, caminhos e tamanhos/hashes registrados dos membros. No S3 batch, só consulta metadados, incluindo controles JSON; lotes usam tamanho/MD5. Nunca baixa corpos de objetos nem índices ausentes. Um ETag multipart sozinho não é MD5 do objeto inteiro. Hash necessário ausente, lote antigo sem checksum de envio ou índice local ausente causam erro explícito. Esta checagem não descompacta membros nem autentica um índice alterado por atacante.

`verify_interval_days` agenda verificações de índices batch; não se aplica a jobs simples. 0 desativa a agenda. Apenas sucesso atualiza `last_verified`. `verify` manual verifica imediatamente. Em jobs simples, executa `rclone check` com as exclusões do job.

## Limites de remoção e ocupação dos discos

Cada job simples define `max_delete` como limite absoluto de arquivos removidos pelo `rclone sync` (padrão 100). O rclone falha e interrompe a sincronização se a execução ultrapassar esse limite. A operação não é repetida inteira após erro, evitando reaplicar o limite em várias tentativas; transferências parciais podem permanecer. Jobs batch mantêm `max_delete_percent` e `deletion_min_files`, calculados pelo índice anterior. `--allow-mass-deletion` ignora a proteção aplicável apenas naquela execução.

Discos da origem, destino local e temporários de lotes causam erro acima de 95% de ocupação, contando espaço indisponível ao usuário. Exatamente 95% é permitido. Destinos inexistentes usam o ancestral existente mais próximo para a medição. Checks ocorrem antes do backup e antes de operações de alteração; `disk_usage` registra porcentagem e bytes livres. S3 não expõe ocupação local. A restauração verifica também o disco de saída.

As opções por job `check_source_disk` e `check_destination_disk` têm padrão true. Use false na origem para filesystems virtuais (como Seafile/FUSE) que informam capacidade zero. A opção do destino também controla a saída de restauração; temporários e proteções de montagem/caminhos continuam ativos. `[[jobs]].continue_on_job_error` tem padrão true, inclusive para falhas de preflight por job; false interrompe na primeira falha. Qualquer falha de job mantém o código de saída diferente de zero. Erros identificam o job na mensagem, no log e nas notificações. Veja a configuração no [guia completo](configuration.pt-BR.md#verificação-de-disco-por-job).

## Resumo, performance e compatibilidade

Jobs batch emitem contagens indexadas de arquivos e lotes. Jobs simples registram a estratégia `rclone sync` e o `max_delete` absoluto por job; uma falha pode ocorrer depois de parte da sincronização ter sido aplicada. Veja [performance](performance.pt-BR.md) para sync nativo e perfis batch.

Estados 0.3.x continuam compatíveis. Lotes sem checksum de envio não têm verificação completa por metadados até serem substituídos após mudanças. Não há reenvio automático só para adicionar o checksum. No modo hash, o cache pressupõe metadados de filesystem confiáveis e força leituras completas periódicas; desative-o quando precisar de leitura completa em toda execução.

## Modos de verificação e progresso no terminal — 0.6.1

Nas cópias e sincronizações interativas, o progresso lê as estatísticas estruturadas do rclone. O terminal mostra arquivos conferidos/copiados, remoções, movimentos e tempo decorrido; um indicador de atividade continua atualizando enquanto aguarda a próxima amostra. O percentual de transferência usa contadores de bytes (ou de arquivos nas transferências de zero bytes). Durante comparação e exclusões anteriores à cópia pode não existir um total confiável: o terminal mostra atividade sem percentual até conhecer os totais de transferência. Essa opção mais rápida não faz inventário adicional. A linha se adapta à largura do terminal, abreviando caminhos e contadores para evitar quebras; o redimensionamento limpa linhas refluídas. Movimentos para o histórico, sozinhos, não indicam transferência concluída. Esses detalhes do terminal ficam fora dos logs JSON/stdout.

`[backup].verification_mode = "simple"` é o padrão; `"hash"` passa `--checksum` aos syncs simples e ativa fingerprints SHA-256/MD5 nos índices batch. O override é `--verification-mode simple|hash`. As opções de cache/leitura completa periódica só se aplicam aos inventários batch.

O stderr interativo exibe etapa, diretório e percentual do backup: jobs simples acompanham estatísticas do `rclone sync`, enquanto fases batch usam contadores ponderados e estimativas limitadas. O percentual cobre o backup inteiro, entre etapas, diretórios e tarefas; só chega a 100% após sucesso. Eventos por arquivo aparecem nos logs JSON ou stdout somente quando o respectivo nível é `detailed`. O progresso não entra nos logs JSON nem stdout e é desativado quando stderr não é um terminal. Veja o [guia completo](configuration.pt-BR.md#retenção-dos-logs).

## Links simbólicos

Links simbólicos nos dados da origem e do destino local são sempre ignorados. Diretórios ligados não são percorridos; links para arquivos, links quebrados e ciclos não interrompem o inventário normal. Os links e seus alvos não são copiados através do link, compactados ou representados como arquivos `.rclonelink`. Um alvo que também apareça como arquivo regular por outro caminho é copiado normalmente.

O rclone ignora links na origem e no destino sem segui-los. Em jobs batch, cópias/históricos indexados desses caminhos ficam protegidos. No sync simples, um link na origem não aparece na árvore de origem, então um arquivo correspondente no destino pode ser considerado removido e movido para `--backup-dir`; continua sujeito ao prazo normal. Históricos simples são removidos por diretório de execução, incluindo caminhos excluídos recentemente dentro de um diretório expirado. Um link no destino local também impede copiar a origem para aquele caminho, sem alterar o link nem seu alvo.

O rclone normalmente ignora links. O Sambinha usa `--skip-links` para suprimir avisos e `--links=false --copy-links=false` para prevalecer sobre variáveis de ambiente que habilitem links. [Documentação oficial do rclone](https://rclone.org/local/#skip-links). Esse comportamento é fixo e não exige configuração.

Caminhos de metadados/estado/trava e a saída da restauração continuam protegidos contra redirecionamento por links. Membros inseguros em lotes e caminhos que mudem para links durante uma transferência ainda podem causar erro explícito, em vez de seguir o novo alvo.
