# Guia completo de configuração e operação

[English](configuration.md) · [README](../README.pt-BR.md)


O Sambinha guarda cópias dos seus arquivos em outra pasta, em um disco externo ou no Amazon S3, um serviço de armazenamento na nuvem. Você escolhe o que quer proteger e onde quer guardar a cópia; o programa cuida de comparar os arquivos, enviar o que mudou e manter um histórico por algum tempo.

## Por que ele existe

Fotos, documentos e projetos costumam ficar espalhados em várias pastas. Fazer uma cópia manual de tudo funciona por um tempo, mas é fácil esquecer uma pasta, gastar espaço com cópias repetidas ou perceber tarde demais que um arquivo importante foi apagado.

O Sambinha foi criado para tornar esse cuidado repetível. Ele registra o que fez em cada execução e evita enviar novamente arquivos que já estão iguais no destino. Se você editar ou apagar algo por engano, as versões anteriores podem continuar disponíveis durante o prazo que você escolheu.

Há também uma preocupação com o custo da nuvem. Guardar milhares de arquivos pequenos separadamente pode ser pouco eficiente no S3. Por isso, o Sambinha pode reuni-los em arquivos compactados, chamados **lotes**, mantendo os caminhos das pastas dentro deles. É como organizar vários documentos em caixas identificadas: você consulta a lista do conteúdo antes de abrir uma caixa.

## Como ele protege seus arquivos

Você pode escolher entre duas maneiras de fazer a cópia. A **cópia simples** deixa cada arquivo no destino com seu nome e sua pasta, o que facilita abrir ou recuperar um documento específico. A **cópia por lotes** reúne arquivos de várias pastas e os compacta com o 7-Zip. Ela é útil quando há muitos arquivos pequenos e você quer reduzir a quantidade de objetos guardados na nuvem.

Ao formar novos lotes, o Sambinha agrupa os arquivos de cada diretório pela data de modificação, da mais antiga para a mais recente. Empates seguem o nome do arquivo. Assim, arquivos antigos tendem a ficar juntos. Nas próximas execuções, os lotes existentes que continuam intactos e respeitam os limites e o formato configurados são preservados, mesmo quando entram arquivos novos ou a ordenação muda. Somente os arquivos novos e os membros dos lotes afetados precisam ser agrupados novamente; o índice local permite reutilizar os demais sem reenviá-los.

As duas maneiras funcionam tanto com uma pasta local quanto com o S3. Também é possível proteger a mesma pasta de duas formas: por exemplo, manter uma cópia simples em um disco externo e uma cópia compactada na nuvem.

Se um arquivo no destino tiver uma data de modificação mais recente que o arquivo da origem, o Sambinha preserva a cópia do destino e interrompe aquela tarefa com erro. O relatório lista os arquivos envolvidos, e o Cronitor recebe um evento de falha com a descrição. Outros backups independentes continuam. Nos lotes, a comparação usa as datas originais registradas no índice confirmado, não a data de envio do arquivo compactado.

Por padrão, arquivos apagados na origem ficam no histórico por **10 dias**, e versões anteriores de arquivos editados ficam por **2 dias**. Esses prazos podem ser alterados. A remoção do histórico vencido acontece nas execuções seguintes; deixar o programa parado não faz a limpeza acontecer sozinha.

Antes de começar, o Sambinha verifica se as ferramentas necessárias estão disponíveis. Durante o envio, tenta novamente até três vezes se uma operação falhar, esperando dois segundos entre as tentativas. Ao terminar, grava um relatório. Se você configurar o Cronitor, um serviço de acompanhamento, ele também pode receber um aviso de sucesso ou erro.

## Prepare o computador

O programa funciona no Linux com **Python 3.12 ou mais recente**. No Linux Mint, você pode usar o Python disponível nos pacotes do sistema. Ele utiliza duas ferramentas conhecidas: o **rclone**, para comparar e copiar arquivos, e o **7-Zip**, para compactar os lotes.

Abra um terminal e instale as ferramentas. O `sudo` é necessário nesta etapa de instalação; os backups depois são executados pela sua conta normal:

```bash
sudo apt install git python3 python3-venv rclone p7zip-full
```

Baixe o projeto e prepare um ambiente separado para ele. Isso evita misturar a instalação do Sambinha com outros programas Python do computador:

```bash
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/sambinha --version
```

A versão atual é **0.8.0**. O repositório é privado, então sua conta precisa ter acesso para baixá-lo. Você também pode baixar o código ou o arquivo de instalação na [página de versões](https://github.com/lesantos/sambinha/releases).

## Faça seu primeiro backup

Comece com uma cópia simples para outra pasta ou disco. Na pasta do projeto, crie um arquivo chamado `sambinha.toml`. Ele é a lista de instruções que o programa vai ler. Este exemplo contém uma única tarefa de backup:

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
log_retention_days = 14
deleted_days = 10
version_days = 2
verification_mode = "simple" # Padrão: tamanho em bytes e data de modificação.

[[jobs]]
name = "meus-documentos"
source = "~/Documentos"
destination = "/mnt/backup/documentos"
type = "local"
logic = "simple"
```

Troque `source` pela pasta que deseja proteger e `destination` pela pasta onde a cópia será guardada. O destino deve ficar fora da pasta de origem. Se ele estiver em um disco externo, conecte e monte o disco antes de executar. Use uma pasta exclusiva para esta tarefa: o Sambinha administra seus arquivos e seu histórico.

`state_dir` guarda o registro que permite ao programa acompanhar os backups. `log_dir` guarda os relatórios de execução. Mantenha essas pastas fora da origem e preserve a pasta de estado entre as execuções. Quando um caminho começa com `~`, ele aponta para sua pasta pessoal; os caminhos relativos, como `./state`, partem do local do arquivo de configuração.

Primeiro, faça uma **simulação**:

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
```

O terminal mostrará o que seria feito. A simulação grava o relatório, mas não cria lotes, envia ou apaga arquivos de backup, nem avisa o Cronitor. Quando a configuração estiver correta, execute a cópia de verdade:

```bash
.venv/bin/sambinha --config sambinha.toml
```

Para atualizar o backup depois de mudar seus arquivos, execute o mesmo comando novamente. Os arquivos iguais serão identificados e não precisarão ser enviados outra vez. Os relatórios ficam na pasta `logs` e registram começo, fim, arquivos copiados, arquivos já iguais e possíveis erros.

Se você preferir executar diretamente a partir do código, pode usar `.venv/bin/python -m sambinha` no lugar de `.venv/bin/sambinha`.

## Leve uma cópia para a nuvem

Quando o backup local estiver funcionando, você pode adicionar outra tarefa para o S3. O [arquivo de exemplo](../sambinha.example.toml) mostra uma mesma origem protegida localmente e na nuvem. Cada tarefa recebe um nome, e você pode executar somente uma delas com `--job NOME`.

Para o S3, você precisará de uma conta AWS, de um **bucket** — o espaço onde os arquivos serão guardados — e de uma configuração do rclone que indique a região e como acessar essa conta. O [guia de S3 e credenciais](s3.pt-BR.md) conduz essa preparação e mostra como manter senhas e tokens fora do Git. O arquivo `sambinha.toml` fica fora dos arquivos versionados, mas os exemplos públicos também devem permanecer sem segredos.

O padrão de armazenamento é `GLACIER_IR`, uma modalidade para dados pouco acessados, com recuperação imediata. Ela pode cobrar pela recuperação e pela exclusão antes do prazo mínimo de armazenamento. As modalidades mais frias exigem uma etapa de restauração na AWS antes de baixar os arquivos. O guia explica essas diferenças para que você escolha a opção adequada, em vez de considerar apenas o preço para guardar os dados.

## Deixe as credenciais em um arquivo privado

Para não precisar carregar as variáveis no terminal toda vez, você pode indicar um arquivo de credenciais no `sambinha.toml`. Acrescente estas linhas à seção `[backup]` que já existe:

```toml
secrets_file = "~/.config/sambinha/secrets.env"
cronitor_url = "${CRONITOR_URL}"
```

Use [secrets.example.env](../secrets.example.env) como modelo e crie o arquivo privado fora da pasta do projeto. Ele pode conter o nome de um perfil AWS que você já configurou e a URL do Cronitor:

```bash
export AWS_PROFILE='sambinha'
export CRONITOR_URL='https://cronitor.link/p/SEU_TOKEN/SEU_MONITOR'
```

Proteja o arquivo com `chmod 600 ~/.config/sambinha/secrets.env`, para que apenas sua conta possa lê-lo e alterá-lo. O [guia de credenciais](s3.pt-BR.md#arquivo-privado-de-variáveis) também mostra como usar chaves AWS diretamente, caso você não tenha um perfil configurado.

Depois disso, execute o comando de backup normalmente. O Sambinha lê o arquivo antes de interpretar os caminhos e iniciar o rclone; você não precisa executar `source`. Se uma variável já estiver definida no terminal, ela tem prioridade. Se o arquivo indicado não existir ou contiver um erro, o programa para antes do backup e informa o problema sem mostrar os valores privados.

O arquivo é lido como dados, sem executar comandos. Você pode usar aspas, comentários e a palavra opcional `export`. Os valores nele são literais; para referenciar uma variável na configuração, use `${NOME}` no TOML, como no exemplo do Cronitor. Caminhos relativos de `secrets_file` partem da pasta do TOML. A opção `--secrets-file CAMINHO` permite escolher outro arquivo naquela execução.

## Escolha o que fica fora do backup

Crie um arquivo de texto UTF-8 ao lado do seu `sambinha.toml`, por exemplo `excludes.txt`. Dentro de cada tarefa (`[[jobs]]`) que deve usar essas regras, configure:

```toml
[[jobs]]
name = "dados-local"
source = "/mnt/dados"
destination = "~/sambinha/Backups/dados"
exclude_files = "./excludes.txt"
```

No `excludes.txt`, coloque um padrão de exclusão do rclone por linha:

```text
# Temporários e logs em qualquer subdiretório
*.tmp
*.log

# Pastas na raiz da origem, incluindo todo o conteúdo
/cache/**
/node_modules/**
```

A **localização do arquivo** é relativa ao `sambinha.toml`, não à pasta atual do terminal. Já os **padrões** são relativos à origem da tarefa. Para a origem `/mnt/dados`, `/cache/**` corresponde a `/mnt/dados/cache/`, e não à pasta `/cache/` do sistema; também não corresponde a `projects/cache/`. `*.tmp` e `*.log` correspondem a arquivos em qualquer subdiretório. Linhas vazias e linhas iniciadas com `#` são ignoradas. A sintaxe é a do rclone, não a de um `.gitignore`.

Os exemplos TOML já configuram `exclude_files = "./excludes.example.txt"`. Esse modelo não tem exclusões ativas: descomente os padrões desejados ou copie o arquivo para `excludes.txt` e ajuste o caminho na tarefa. Há também um [modelo em português](../excludes.example.pt-BR.txt).

Confira a quantidade de arquivos selecionados e o trabalho previsto com uma simulação:

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
```

Os arquivos excluídos não são enviados e não são tratados como arquivos apagados. Cópias já existentes no destino **não são apagadas só por passarem a ser excluídas**; seus históricos ficam protegidos enquanto a regra existir. Um lote antigo que misture arquivos incluídos e excluídos é mantido à parte, no índice `excluded`; os lotes atuais contêm somente a seleção atual. Cada tarefa pode usar um arquivo de regras diferente. Arquivo ausente, ilegível ou com UTF-8 inválido interrompe o backup. Alterações no arquivo durante uma execução só valem na próxima. O [guia de uso](usage.pt-BR.md#exclusões-por-arquivo-de-regras) detalha esse comportamento.

## Confirme que os discos estão disponíveis

Para um backup em discos montados, informe na tarefa `source_mount` e `destination_mount`: são os pontos onde os discos aparecem no Linux, não necessariamente as subpastas copiadas. Por exemplo, um destino `/mnt/capsule/LLLD/linux` usa `destination_mount = "/mnt/capsule/LLLD"`.

O Sambinha aplica a mesma estratégia do seu comando `mounted_nonempty`: o ponto precisa passar em `mountpoint -q` e conter pelo menos uma entrada. Se ele estiver ausente, desmontado, vazio ou ilegível, a execução para antes de copiar e envia erro ao Cronitor. A conferência se repete antes das cópias e exclusões. Configure o ponto da origem e, para destinos locais, o do destino; o S3 tem somente a origem local.

Para pastas comuns que não dependem de um disco montado específico, as opções podem ficar ausentes. O [guia de montagem](usage.pt-BR.md#verificação-dos-discos-montados) mostra a configuração completa e como localizar o ponto correto.

## Saiba com antecedência o que será apagado

Quando mais de 1000 arquivos ou versões do histórico estiverem próximos da exclusão definitiva, o Sambinha envia um evento de erro ao Cronitor e lista no relatório os caminhos originais, os objetos de backup e as datas de exclusão. Esse aviso não bloqueia o backup nem altera o prazo: a execução continua e retorna sucesso se não houver outro problema.

O aviso começa dois dias antes do vencimento. Para retenção de um dia, começa um dia antes; para retenção zero, só pode ocorrer na própria execução. Configure `deletion_alert_threshold` em `[backup]` para mudar o limite. Arquivos excluídos pelas regras de seleção ficam fora desse alerta. Execute o programa diariamente para receber o aviso na janela prevista; se ficar parado até o vencimento, o aviso será emitido na próxima execução e a limpeza seguirá o prazo original.

## Como recuperar um arquivo

Na cópia simples, os arquivos atuais estão no destino com a mesma organização da origem. Você pode copiá-los de volta como faria com arquivos de qualquer outra pasta. Dentro do destino, a pasta oculta `.sambinha` guarda o histórico e os registros do programa; ela não faz parte dos seus documentos atuais.

Na cópia por lotes, você consulta os arquivos de controle para descobrir em qual lote está o documento e depois extrai o arquivo compactado com o 7-Zip. O Sambinha também envia uma cópia compactada do índice de controle para o destino, para ajudar na recuperação se o computador de origem for perdido.

O [guia de configuração e recuperação](usage.pt-BR.md) mostra os caminhos do histórico e os passos para recuperar arquivos e o índice. Ele também explica os limites dos lotes e os cuidados para atualizar uma instalação anterior. Conhecer esses passos antes de precisar deles ajuda a escolher a forma de backup mais conveniente.

## Como testar o programa

Os testes fazem backups de arquivos pequenos, recuperam as cópias e comparam seus caminhos e seu conteúdo com a origem. Isso inclui cópias simples e por lotes, tanto locais quanto no S3. Para o S3, usam o **Moto**, um simulador da AWS que roda no próprio computador: seus dados e suas credenciais reais não são usados.

Prepare as ferramentas de teste no mesmo ambiente do projeto e execute a suíte completa, incluindo o servidor S3 simulado:

```bash
.venv/bin/python -m pip install -r requirements-test.txt
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*'
```

O resultado deve terminar em `OK`. A última linha da tabela de cobertura mostra quanto do código os testes percorreram. As verificações incluem arquivos vazios, nomes com acentos, arquivos editados ou apagados, falhas de envio, recuperação após falhas e simulação. Os arquivos gerados têm no máximo **100 KiB**, e as pastas temporárias são removidas ao final.

O [relatório da versão atual](test-results.pt-BR.md) registra os resultados obtidos. A cada envio de código, o GitHub também executa esses testes automaticamente; você pode acompanhar o resultado na [página de testes do projeto](https://github.com/lesantos/sambinha/actions).

## Para continuar

Todas as opções públicas de configuração estão em [sambinha.example.toml](../sambinha.example.toml), inclusive as opcionais comentadas; um teste confere essa lista com o código. Depois do primeiro backup, o [guia de uso](usage.pt-BR.md) ajuda a ajustar prazos, limites e várias tarefas. O [guia de S3](s3.pt-BR.md) trata da nuvem e das credenciais. Para quem quiser contribuir com o código, a [arquitetura](architecture.pt-BR.md) explica como o projeto está organizado, e as [notas da versão](../CHANGELOG.pt-BR.md) registram as mudanças.

As orientações e os resultados da auditoria de credenciais estão em [SECURITY.md](../SECURITY.pt-BR.md). A CI também verifica o histórico completo para detectar novas credenciais no código.

## Jobs e execuções em paralelo

Os jobs executam sequencialmente, na ordem do TOML, depois dos inventários do preflight. As opções de concorrência atuam dentro de cada job. Backups e verificações normais obtêm uma trava exclusiva sem espera em `state_dir`: uma segunda execução usando o mesmo diretório de estado termina com erro, antes de alterar dados do backup. Isso também vale para seleções `--job` diferentes. Simulações e restaurações não obtêm essa trava de escrita. Diretórios de estado independentes podem executar em paralelo e devem administrar destinos separados.

## Retenção dos logs

`[backup].log_retention_days = 14` mantém os logs de auditoria do Sambinha escritos nos últimos 14 dias. Escolha outro número de dias ou `0` para guardar os logs indefinidamente. `--log-retention-days 30` substitui a configuração para uma execução. Backups, restaurações e verificações sem simulação removem apenas arquivos de log regulares e inativos com o formato de nome timestamp/UUID `.jsonl` do Sambinha, diretamente em `log_dir`. A idade conta da última escrita, não da data no nome. O log atual, logs abertos por outras execuções, links simbólicos, subdiretórios e arquivos de outros tipos são preservados. Simulações não removem logs. A limpeza depende das execuções do programa; não instala um timer separado.

```toml
[backup]
log_retention_days = 14
```


## Verificação de disco por job

As opções `check_source_disk` e `check_destination_disk` ficam dentro de cada `[[jobs]]` e têm padrão `true`. Use `false` para suprimir a respectiva verificação de capacidade durante backup e verificação. Para uma origem virtual como Seafile/FUSE que informa capacidade zero, desative apenas a checagem da origem:

```toml
[[jobs]]
name = "seafile-data"
source = "/opt/seafile/seafile-fuse"
destination = "/mnt/backup/seafile"
type = "local"
logic = "simple"
check_source_disk = false
check_destination_disk = true
```

Use `check_destination_disk = false` para suprimir também a checagem de capacidade do destino local. Essa opção inclui a pasta de saída da restauração. Destinos S3 não têm checagem de capacidade local. O espaço temporário usado na criação de lotes, verificação e restauração continua sendo verificado. Essas opções não desativam checagens de montagem, leitura da origem, validação de caminhos ou proteção contra remoções em massa. Falhas reais de escrita, incluindo falta de espaço, continuam causando erro no job. Com as checagens ativadas, capacidade indisponível causa erro; a mensagem indica o caminho e o motivo.

## Continuar ou parar após falha em um job

`continue_on_job_error = true` é uma opção por job, dentro de cada `[[jobs]]`, e esse é o padrão: se esse job falhar, a execução continua nos próximos jobs independentes, inclusive em falhas durante a leitura das exclusões ou as checagens e inventários do preflight. Use `false` em um job cuja falha deve interromper a execução. A decisão usa a opção do job que falhou; cada job pode ter uma política diferente:

```toml
[[jobs]]
name = "critico"
source = "~/Documentos"
destination = "/mnt/backup/critico"
continue_on_job_error = false

[[jobs]]
name = "opcional"
source = "~/Fotos"
destination = "/mnt/backup/opcional"
continue_on_job_error = true
```

Isso vale para backup e verificação manual. O preflight verifica todos os jobs selecionados antes de alterar os backups; se um job com `false` falhar nessa etapa, todas as transferências são impedidas. Uma falha posterior em um job com `false` interrompe os próximos jobs sem desfazer o trabalho concluído. Jobs com `true` podem falhar e ser ignorados enquanto os demais jobs válidos executam. Em ambos os casos, qualquer falha deixa o código de saída diferente de zero. Falhas compartilhadas, como ausência do executável rclone ou indisponibilidade da trava do estado, interrompem a execução independentemente dessa opção. Os erros incluem `Job NOME:` na mensagem e o campo `job` no log JSON, inclusive no preflight; as notificações de falha também identificam o job.

## Escolha a verificação simples ou por hash

**`verification_mode = "simple"` é o padrão na versão 0.6.1**, inclusive em configurações existentes que omitam a opção. Compara tamanho em bytes e timestamp de modificação (`mtime`, com precisão de nanossegundos). O inventário consulta os metadados sem ler o conteúdo dos arquivos, e a listagem do destino não calcula hashes. O conteúdo ainda precisa ser lido para copiar ou compactar os arquivos.

Para comparar o conteúdo por SHA-256 e MD5, configure:

```toml
[backup]
verification_mode = "hash"
```

Também é possível escolher o modo para uma execução:

```bash
.venv/bin/sambinha --config sambinha.toml --verification-mode simple
.venv/bin/sambinha --config sambinha.toml --verification-mode hash
```

O modo simples não detecta uma edição que preserve simultaneamente tamanho e data de modificação. O modo hash lê o conteúdo no primeiro inventário e periodicamente depois. Trocar de simples para hash calcula hashes novos; trocar o modo em backups por lotes pode reconstruir os arquivos compactados porque os metadados do índice mudam. Para verificar por hash um índice que ainda não os possui, faça primeiro um backup no modo hash. `logic = "simple"`/`"batch"` escolhe a estratégia de armazenamento, independentemente de `verification_mode`. Lotes e controles de recuperação mantêm checksums nos dois modos.

Durante a execução interativa, o stderr exibe o percentual estimado do backup inteiro no começo da linha, seguido da etapa e do subdiretório em trabalho. A estimativa reserva 20% para verificações/inventários, 75% para tarefas ponderadas pelos bytes da origem e 5% para finalização. Contadores de arquivos/bytes, filas de diretórios e estimativas limitadas por tempo decorrido avançam dentro dessas faixas. Lotes concorrentes contribuem proporcionalmente aos bytes. O percentual não volta nem reinicia ao mudar diretório, etapa ou tarefa, e chega a 100% somente após conclusão bem-sucedida. Cópias agrupadas ficam no mesmo diretório para indicar a pasta realmente copiada. Eventos de cada arquivo permanecem no log JSON, mas não aparecem no terminal interativo; resumos e erros continuam visíveis. Execuções sem terminal mantêm os eventos JSON no stdout para automação. Progresso não entra nos logs JSON nem stdout e fica desativado quando stderr não é um terminal.

## Recursos adicionais

A versão atual é **0.8.0**. A documentação padrão é em inglês; este arquivo e os guias `.pt-BR` mantêm o português do Brasil. Os exemplos públicos também têm versões comentadas nos dois idiomas.

### Restaurar arquivos ou pastas

```bash
.venv/bin/sambinha restore --job documents --path projects --output /mnt/recovery/documents
.venv/bin/sambinha restore --job documents --path notes.md --output /mnt/recovery/notes
.venv/bin/sambinha restore --job documents --output /mnt/recovery/all
```

`--path` aceita repetição e usa caminhos literais do índice ativo. A pasta de saída deve ser nova ou vazia, separada de origens, backups, estado e logs. A restauração verifica tamanho em uma área temporária antes de publicar os arquivos; confere SHA-256 e MD5 quando registrados no índice. Lotes também passam pela verificação de checksum e integridade do 7-Zip. Preserva datas e permissões comuns e não sobrescreve dados existentes nem aplica bits privilegiados. Não precisa dos arquivos originais. Para lotes, pode recuperar o índice perdido a partir de `.sambinha/control.tar.gz`; a cópia simples exige o índice local. Histórico de versões anteriores continua sendo recuperado manualmente pelo guia.

Restaurar do S3 baixa os objetos selecionados e pode gerar custos de recuperação; selecionar um membro de lote baixa o lote inteiro. GLACIER/DEEP_ARCHIVE exigem uma restauração prévia pela AWS.

### Verificar sem baixar objetos do S3

```bash
.venv/bin/sambinha verify --job documents
```

Localmente, cópias usam tamanho/data no modo simples e leitura do conteúdo no modo hash. Lotes são testados e extraídos para conferir os arquivos originais, incluindo hashes quando registrados. No S3, faz **somente consultas de metadados**: cópias usam tamanho/data no modo simples e tamanho/MD5 no modo hash; lotes e controles JSON sempre usam tamanho/MD5. **Nunca baixa corpos de objetos nem o índice de recuperação**, inclusive nas falhas. Consultas podem ter cobrança de API; essa verificação não testa a extração dos membros de lotes.

Novos lotes registram tamanho compactado e checksum de envio. Lotes antigos sem esses campos, índice local ausente ou hash remoto indisponível causam erro explícito, sem download alternativo ou reconstrução automática de lotes intactos. Preserve os metadados de checksum do rclone para uploads multipart.

Configure `[backup].verify_interval_days = 7` para verificar após backups bem-sucedidos quando o prazo vencer; a primeira execução habilitada verifica imediatamente. O padrão 0 desativa a agenda por compatibilidade. A agenda depende das execuções de backup, sem instalar um timer separado. Falhas mantêm a data da última verificação bem-sucedida.

### Proteção contra remoções e monitoramento dos discos

Cada tarefa bloqueia, antes de mudar o destino, remoções acima de `max_delete_percent = 50` quando o índice selecionado anterior tem pelo menos `deletion_min_files = 20` arquivos. Exclusões não entram no cálculo. Use `--allow-mass-deletion` somente para autorizar uma remoção intencional naquela execução; a opção não fica gravada. O aviso consultivo de retenção continua separado desse bloqueio.

Por padrão, discos da origem e do destino local geram erro com ocupação **acima de 95%**; exatamente 95% é aceito. Espaço indisponível à conta também entra na ocupação. Os discos temporários dos lotes e a saída de restauração são verificados. Um bucket S3 não oferece porcentagem de ocupação de disco local. Há verificações antes do backup e antes de transferências/exclusões, e eventos `disk_usage` registram espaço livre e ocupação.

Cada tarefa emite `summary`: arquivos novos, modificados, removidos e intactos, lotes reutilizados/recriados, bytes originais previstos, bytes de conteúdo realmente transferidos, histórico pendente e sucesso. Compactação pode mudar a quantidade de bytes enviados; controles e índice não entram nessa medida de conteúdo.

### Performance e perfis de compactação

P1 a P6 estão implementadas: cache de fingerprints com leitura completa periódica; transferências simples agrupadas; inventário compartilhado para origens/seleções iguais; compactação e envio de lotes com concorrência limitada; cache dos filtros na execução; níveis e threads por categoria. Confira o [guia de performance](performance.pt-BR.md).

```toml
[backup]
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_group_size = 100
transfer_workers = 4
batch_workers = 2

[[jobs]]
name = "media"
source = "~/Files"
destination = "/mnt/backup/media"
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

No **modo hash**, o cache exige dispositivo, inode, tamanho, datas de modificação/alteração em nanossegundos e permissões iguais. A leitura completa ocorre a cada sete dias por padrão; `--no-fingerprint-cache` desativa o reaproveitamento. No **modo simples**, essas opções não ativam hashes: as conferências usam tamanho e data de modificação. Arquivos alterados continuam validados antes/depois dos envios conforme o modo escolhido. Novos lotes separam categorias sem mudar a ordem por data dentro do diretório. Isso pode criar lotes individuais em categorias intercaladas. Lotes antigos intactos mantêm sua configuração; novos ajustes valem na criação/recriação. Nível 0 não compacta; 1–9 aumentam o esforço. Threads vão de 1 a 64 por arquivo compactado, dependendo do suporte do algoritmo.

Metadados e logs têm permissões privadas. O índice é validado antes da retenção e restauração; caminhos inseguros, links e conflitos entre dados ativos e histórico são rejeitados. Credenciais conhecidas são ocultadas em erros, logs e telemetria. Consulte [segurança](../SECURITY.pt-BR.md).
