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

Na lógica simples, o destino apresenta os caminhos originais. Antes de substituir ou apagar um arquivo, a versão anterior é copiada para `.sambinha/history/version/<id>/...` ou `.sambinha/history/deleted/<id>/...`. Na lógica por lotes, arquivos compactados antigos permanecem no caminho original até expirar; o índice separa `active` e `retired`.

A contagem começa quando uma execução bem-sucedida detecta a alteração/remoção. A limpeza ocorre durante execuções seguintes, inclusive no instante exato da expiração. Retenção zero permite apagar o histórico nessa execução. Um lote com arquivos editados e apagados usa o maior prazo aplicável. Falhas interrompem a operação afetada; preservam o último índice confirmado. Uploads interrompidos podem deixar objetos sem referência; não são apagados automaticamente para evitar perda de dados.

Para recuperar `simple`, copie os arquivos atuais do destino, excluindo `.sambinha/`; consulte o histórico para versões antigas. Para recuperar `batch`:

- Baixe `.sambinha/control.tar.gz` e extraia `state.json` e `controls/` para um diretório separado.
- Consulte as chaves em `state.json.active` e baixe **somente os lotes ativos**; arquivos `retired` contêm versões anteriores.
- Para classes com restore, solicite a restauração na AWS antes do download.
- Extraia com `7z x arquivo.7z -o/pasta/restaurada`. Os caminhos internos já incluem a árvore a partir da raiz da origem. Compare SHA-256 e tamanho com os controles.
- Para recuperar o estado local, reponha `state.json` e `controls/` em `<state_dir>/<job>/`, usando a mesma origem/destino/lógica da identidade no índice.

`batch` confia no espelho local para evitar chamadas S3 de inventário. Exclusões/corrupção feitas externamente no destino precisam ser corrigidas recuperando/reconstruindo o índice; não apague o estado local. A cópia `simple` compara tamanho/data por padrão; no modo hash, consulta checksums do destino e repara divergências de conteúdo. No modo hash, destinos sem MD5 disponível falham explicitamente. O rclone verifica transferências conforme o modo escolhido e as garantias do backend.

Links simbólicos são ignorados. Arquivos especiais e nomes com quebras de linha são rejeitados com erro explícito. Cada job deve manter sua própria origem/destino separados, e todos devem ficar fora dos diretórios de estado/logs. Sobreposições entre jobs são permitidas; veja [as regras de interação](configuration.pt-BR.md#jobs-sequenciais-e-caminhos-sobrepostos). `.sambinha` na raiz da origem é reservado. Atributos locais não exigem troca de proprietário; S3 não oferece permissões POSIX nativas. A simulação cria apenas o log e faz leitura/preflight; não cria lotes/estado, transfere ou apaga arquivos, nem notifica Cronitor.


## Atualizando para a versão 0.2.0

Esta versão usa o comando `sambinha`, o arquivo `sambinha.toml` e a pasta de metadados `.sambinha`. Atualize os comandos de execução e os agendamentos que você tiver criado. A versão anterior continua disponível nas suas cópias já instaladas até que você a remova.

Para preservar backups feitos por uma instalação anterior, use um novo destino e uma nova pasta de estado para o primeiro backup da versão 0.2.0. Mantenha o destino e o estado anteriores juntos para consultar ou restaurar esse histórico. O programa recusa reutilizar um estado de outro formato de metadados; ele não converte arquivos remotos automaticamente. Esta mudança não exige reenviar dados para testar: comece sempre com `--dry-run`.

## Arquivos mais novos no destino

Antes de modificar uma tarefa, a cópia simples compara as datas de modificação do inventário rclone com as da origem, com precisão de nanossegundos. Uma data mais recente no destino impede todas as alterações daquela tarefa, inclusive quando os bytes são iguais; o arquivo e sua data permanecem intactos. Datas iguais permitem comparar tamanho/data no modo simples padrão ou conteúdo por checksum no modo hash. Se o destino não fornecer uma data válida, a tarefa também falha antes de alterar os arquivos.

Nos lotes, a comparação considera as datas originais dos arquivos no último índice confirmado. A data de upload dos lotes não é a data de edição dos documentos. Conflitos aparecem como `newer_destination` nos logs, e a execução termina com código 1 e um evento Cronitor `fail` com a descrição dos arquivos. A simulação registra o conflito e retorna erro, sem enviar notificações.

Nas transferências simples, o comando `copyto` também recebe `--update`. Se o rclone pular um arquivo porque o destino foi atualizado durante a execução, o Sambinha confirma a data novamente e transforma esse conflito em erro, sem alterar o arquivo mais novo.

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

A seleção usa `rclone lsjson --exclude-from` tanto para a origem quanto para o destino simples. A mesma seleção alimenta a compactação por lotes, sem enviar arquivos excluídos. Não há uma segunda implementação dos padrões em Python. Os nomes do histórico são avaliados pelo próprio rclone em uma árvore temporária de arquivos vazios, sem baixar lotes remotos.

Arquivos excluídos já existentes no destino simples são preservados. Históricos que contenham algum arquivo excluído também são preservados. Um lote ativo antigo com membros excluídos vai para `state.json.excluded`, mantendo seu arquivo e controle no destino e na cópia de recuperação; não é um lote atual da seleção filtrada. Isso evita apagar parte de um lote que não pode ser dividida sem restauração.

Remover uma regra volta a incluir esses caminhos nas próximas execuções. Históricos já vencidos podem ser removidos ao voltar à seleção; seus prazos originais não são reiniciados. A simulação mostra somente arquivos selecionados e não altera estado ou destino. Sem `exclude_files`, o comportamento anterior permanece.

As regras são copiadas para um arquivo temporário no início de cada job, mantendo a mesma seleção na origem, no destino e no histórico daquele job. Uma alteração nas regras de um job posterior será observada quando ele começar.

## Alerta antecipado de exclusões definitivas

```toml
[backup]
deletion_alert_threshold = 1000
```

A opção equivalente na linha de comando é `--deletion-alert-threshold`. O limite é estrito: exatamente 1000 não dispara; mais de 1000 dispara. A contagem é independente por job e conta os arquivos/versões dentro dos lotes, não apenas a quantidade de arquivos compactados. É possível configurar 0 para avisar sobre qualquer exclusão definitiva prevista.

Cada histórico novo registra a data de criação, a retenção, os caminhos originais, o vencimento e o início do aviso. O período é de dois dias para retenções de dois ou mais dias, um dia para retenção de um dia e zero para retenção imediata. O programa precisa ser executado regularmente para avisar na janela: ele não envia eventos enquanto estiver parado e não adia exclusões para compensar uma execução perdida.

Quando o limite é ultrapassado, os eventos `deletion_alert` e `deletion_candidate` listam os arquivos e as datas. `deletion_scheduled` registra o planejamento quando o histórico é criado; `expire` registra a remoção efetiva. Históricos protegidos por exclusões não entram na contagem. O Cronitor recebe `fail` com o resumo, mas o código de saída continua 0 e `end.success` continua verdadeiro se não houver falha real. Não é necessário confirmar a exclusão: ela ocorre no prazo que já estava configurado. O aviso pode repetir nas execuções da janela, até a limpeza terminar.

Na cópia simples, um arquivo apagado pode sair da árvore atual antes disso, mas sua cópia permanece no histórico durante a retenção. O aviso trata da remoção definitiva dessa cópia. Em simulação, o planejamento aparece nos logs e nenhuma notificação é enviada.

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

A origem pode estar ausente. A cópia simples precisa do índice local; lotes podem recuperar `state.json` do tar remoto, lendo apenas o membro regular limitado a 64 MiB, sem extrair outros caminhos ou instalar um estado novo. `restore` recupera o índice ativo; versões históricas continuam sendo recuperadas manualmente. Restaurar do S3 baixa os arquivos/lotes selecionados e pode gerar cobrança. GLACIER/DEEP_ARCHIVE exigem restore prévio na AWS.

`verify` de cópias segue `verification_mode`: o padrão `"simple"` usa tamanho/data em nanossegundos; `"hash"` lê conteúdo local ou consulta MD5 no S3. Lotes locais validam checksum do arquivo compactado, integridade do 7-Zip, caminhos e tamanhos/hashes registrados dos membros. No S3, só consulta metadados, incluindo controles JSON; lotes sempre usam tamanho/MD5. Nunca baixa corpos de objetos nem índices ausentes. Um ETag multipart sozinho não é MD5 do objeto inteiro. Hash necessário ausente, lote antigo sem checksum de envio ou índice local ausente causam erro explícito. Esta checagem não descompacta membros nem autentica um índice alterado por atacante.

`verify_interval_days` é global: 0 desativa; 7 verifica após backups bem-sucedidos quando devido, incluindo a primeira execução habilitada. Apenas sucesso atualiza `last_verified`; falha notifica Cronitor e retorna erro. Não há timer independente. `verify` manual verifica imediatamente e não altera a data da agenda automática. Simulação informa a verificação prevista sem ler conteúdo ou baixar objetos.

## Bloqueio de remoções em massa e ocupação dos discos

As opções por tarefa `max_delete_percent = 50` e `deletion_min_files = 20` bloqueiam quando a seleção anterior atinge o mínimo e a porcentagem removida fica estritamente acima do limite. O cálculo ignora caminhos excluídos e usa o índice confirmado anterior. O bloqueio ocorre antes de alterar histórico, enviar, substituir ou limpar retenção. O primeiro backup não tem base anterior; exatamente o limite é permitido. Use mínimo 1 para proteger também pastas pequenas. `--allow-mass-deletion` autoriza apenas uma execução; confira a origem antes de usá-lo. Outras tarefas independentes continuam.

Discos da origem, destino local e temporários de lotes causam erro acima de 95% de ocupação, contando espaço indisponível ao usuário. Exatamente 95% é permitido. Destinos inexistentes usam o ancestral existente mais próximo para a medição. Checks ocorrem antes do backup e antes de operações de alteração; `disk_usage` registra porcentagem e bytes livres. S3 não expõe ocupação local. A restauração verifica também o disco de saída.

As opções por job `check_source_disk` e `check_destination_disk` têm padrão true. Use false na origem para filesystems virtuais (como Seafile/FUSE) que informam capacidade zero. A opção do destino também controla a saída de restauração; temporários e proteções de montagem/caminhos continuam ativos. `[[jobs]].continue_on_job_error` tem padrão true, inclusive para falhas de preflight por job; false interrompe na primeira falha. Qualquer falha de job mantém o código de saída diferente de zero. Erros identificam o job na mensagem, no log e nas notificações. Veja a configuração no [guia completo](configuration.pt-BR.md#verificação-de-disco-por-job).

## Resumo, performance e compatibilidade

Cada tarefa emite `summary` com arquivos totais/novos/modificados/intactos/removidos, lotes reutilizados/recriados, bytes originais previstos, bytes de conteúdo transferidos e exclusões históricas pendentes. Custos dos controles/índice ficam fora dos bytes de conteúdo. Falhas podem ter transferências parciais; não há transação distribuída entre tarefas. Veja as configurações P1–P6 em [performance](performance.pt-BR.md).

Estados 0.3.x continuam compatíveis. Lotes sem checksum de envio não têm verificação completa por metadados até serem substituídos após mudanças. Não há reenvio automático só para adicionar o checksum. No modo hash, o cache pressupõe metadados de filesystem confiáveis e força leituras completas periódicas; desative-o quando precisar de leitura completa em toda execução.

## Modos de verificação e progresso no terminal — 0.6.1

`[backup].verification_mode = "simple"` é o padrão, inclusive em configurações existentes sem essa chave; `"hash"` ativa SHA-256/MD5. O override é `--verification-mode simple|hash`. As opções de cache/leitura completa periódica só se aplicam ao modo hash. Trocar para hash recalcula hashes ausentes; trocar modos em backups por lotes pode reconstruir arquivos compactados. Verificação por hash de um índice sem hashes exige antes um backup nesse modo. Restauração confere tamanhos e hashes quando presentes no índice; checksums dos lotes são mantidos nos dois modos.

O stderr interativo exibe etapa, diretório e percentual do backup: arquivos/bytes para inventários, fila conhecida na descoberta de diretórios, percentual de transferência do rclone nas cópias em lote e tempo decorrido em outras operações opacas. O percentual cobre o backup inteiro, entre etapas, diretórios e tarefas; não volta e só chega a 100% após sucesso. Eventos por arquivo ficam na auditoria e são ocultados do terminal interativo. Cópias agrupadas mostram o subdiretório real. O progresso não entra nos logs JSON nem stdout e é desativado quando stderr não é um terminal. Veja o [guia completo](configuration.pt-BR.md#escolha-a-verificação-simples-ou-por-hash).

## Links simbólicos

Links simbólicos nos dados da origem e do destino local são sempre ignorados. Diretórios ligados não são percorridos; links para arquivos, links quebrados e ciclos não interrompem o inventário normal. Os links e seus alvos não são copiados através do link, compactados ou representados como arquivos `.rclonelink`. Um alvo que também apareça como arquivo regular por outro caminho é copiado normalmente.

Esses caminhos funcionam como exclusões implícitas: cópias, lotes e históricos existentes ficam protegidos enquanto o caminho correspondente na origem ou no destino contiver um link. Um link no destino local também impede copiar a origem para aquele caminho, sem alterar o link nem seu alvo.

O rclone normalmente ignora links. O Sambinha usa `--skip-links` para suprimir avisos e `--links=false --copy-links=false` para prevalecer sobre variáveis de ambiente que habilitem links. [Documentação oficial do rclone](https://rclone.org/local/#skip-links). Esse comportamento é fixo e não exige configuração.

Caminhos de metadados/estado/trava e a saída da restauração continuam protegidos contra redirecionamento por links. Membros inseguros em lotes e caminhos que mudem para links durante uma transferência ainda podem causar erro explícito, em vez de seguir o novo alvo.
