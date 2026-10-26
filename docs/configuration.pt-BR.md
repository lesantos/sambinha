# Guia completo de configuração e operação

[English](configuration.md) · [README](../README.pt-BR.md)


O Sambinha guarda cópias dos seus arquivos em outra pasta, em um disco externo ou no Amazon S3 e registra cada execução. Jobs simples delegam comparação, exclusões e sincronização ao `rclone sync`; arquivos substituídos ou apagados vão para `--backup-dir`. Jobs batch mantêm o fluxo de arquivos compactados com índice.

## Por que ele existe

Fotos, documentos e projetos costumam ficar espalhados em várias pastas. Fazer uma cópia manual de tudo funciona por um tempo, mas é fácil esquecer uma pasta, gastar espaço com cópias repetidas ou perceber tarde demais que um arquivo importante foi apagado.

O Sambinha foi criado para tornar esse cuidado repetível. Ele registra o que fez em cada execução e evita enviar novamente arquivos que já estão iguais no destino. Se você editar ou apagar algo por engano, as versões anteriores podem continuar disponíveis durante o prazo que você escolheu.

Há também uma preocupação com o custo da nuvem. Guardar milhares de arquivos pequenos separadamente pode ser pouco eficiente no S3. Por isso, o Sambinha pode reuni-los em arquivos compactados, chamados **lotes**, mantendo os caminhos das pastas dentro deles. É como organizar vários documentos em caixas identificadas: você consulta a lista do conteúdo antes de abrir uma caixa.

## Como ele protege seus arquivos

Você pode escolher entre duas maneiras de fazer a cópia. A **cópia simples** deixa cada arquivo no destino com seu nome e sua pasta, o que facilita abrir ou recuperar um documento específico. A **cópia por lotes** reúne arquivos de várias pastas e os compacta com o 7-Zip. Ela é útil quando há muitos arquivos pequenos e você quer reduzir a quantidade de objetos guardados na nuvem.

Novos lotes usam percurso em profundidade, ordenando arquivos e subpastas juntos por data de alteração (antigos primeiro, desempate pelo nome). Arquivos grandes recebem lotes individuais sem fechar o lote normal. Não há separação por categoria ou limite de quantidade. A associação dos membros confirmados permanece estável mesmo com crescimento ou remoção; somente rebalanceamento manual redistribui grupos acima do limite. Veja [lotes persistentes e recuperação](batch-lifecycle.pt-BR.md).

As duas maneiras funcionam tanto com uma pasta local quanto com o S3. Também é possível proteger a mesma pasta de duas formas: por exemplo, manter uma cópia simples em um disco externo e uma cópia compactada na nuvem.

Nos jobs batch, um arquivo mais novo no destino causa erro antes da substituição. Nos jobs simples, `--update` do rclone preserva arquivos que ele considera mais novos. Outros backups independentes continuam.

Jobs batch mantêm arquivos apagados por **10 dias** e versões substituídas por **2 dias**. Jobs simples removem diretórios expirados de `--backup-dir` após o maior valor entre `deleted_days` e `version_days`, pois o rclone não identifica se cada arquivo foi apagado ou substituído. A limpeza lista somente diretórios imediatos do histórico e apaga os diretórios de execução expirados.

Em jobs batch no S3, `storage_class` define a classe dos arquivos `.7z`, e `control_storage_class` define a classe dos JSONs e índices de recuperação. Os controles usam `STANDARD` por padrão para que os metadados necessários para listar e restaurar backups continuem imediatamente acessíveis; cada job escolhe sua classe de lotes.

Os logs agrupam eventos da execução inteira; a limpeza usa o maior `log_retention_days` entre os jobs configurados para preservar o prazo solicitado por todos.

Antes de começar, o Sambinha verifica se as ferramentas necessárias estão disponíveis. Durante o envio, tenta novamente até três vezes se uma operação falhar, esperando dois segundos entre as tentativas. Ao terminar, grava um relatório. Se você configurar o Cronitor, um serviço de acompanhamento, ele também pode receber um aviso de sucesso ou erro.

## Prepare o computador

O programa funciona no Linux e no macOS com **Python 3.12 ou mais recente**. No Linux Mint, você pode usar o Python disponível nos pacotes do sistema; no macOS, veja as [observações sobre macOS](#macos) abaixo. Ele utiliza duas ferramentas conhecidas: o **rclone**, para comparar e copiar arquivos, e o **7-Zip**, para compactar os lotes.

Abra um terminal e instale as ferramentas. O `sudo` é necessário nesta etapa de instalação; os backups depois são executados pela sua conta normal:

```bash
sudo apt install git python3 python3-venv
```

São necessários **rclone 1.60 ou mais novo** e **7-Zip 22.00 ou mais novo** (`7zz`); o Sambinha se recusa a rodar com versões anteriores. Ubuntu 22.04 e Linux Mint 21 trazem rclone 1.53 e p7zip 16.02, que são antigos demais: instale os binários oficiais de <https://rclone.org/downloads/> e <https://www.7-zip.org/download.html> (`7zz` para Linux x64) em `/usr/local/bin`, ou, no Ubuntu 24.04/Debian 13, use `sudo apt install rclone 7zip`. Depois configure `compressor = "7zz"` nos jobs em lotes e confira com `rclone version` e `7zz`.

Baixe o projeto e prepare um ambiente separado para ele. Isso evita misturar a instalação do Sambinha com outros programas Python do computador:

```bash
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/sambinha --version
```

A versão atual é **0.14.9**. O repositório é privado, então sua conta precisa ter acesso para baixá-lo. Você também pode baixar o código ou o arquivo de instalação na [página de versões](https://github.com/lesantos/sambinha/releases).

### macOS

Instale as ferramentas com o [Homebrew](https://brew.sh): `brew install git python@3.12 rclone sevenzip`. O compactador se chama `7zz`; configure `compressor = "7zz"` nos jobs em lotes. Depois siga os mesmos passos de `git clone`, `python3 -m venv .venv` e `pip install .` acima.

O que muda em relação ao Linux:

- `source_mount` e `destination_mount` são conferidos comparando o dispositivo do ponto de montagem com o do diretório pai, pois o macOS não tem o comando `mountpoint`. Discos externos aparecem em `/Volumes/NOME`; use `destination_mount = "/Volumes/NOME"`.
- A detecção do sistema de arquivos lê a tabela do comando `mount` em vez de `/proc/self/mountinfo`. Volumes APFS e HFS+ recebem metadados POSIX completos (`--metadata`); volumes FAT (`msdos`) e exFAT mantêm o mesmo tratamento conservador do Linux. Montagens SMB, AFP e outras de rede nunca pedem metadados estendidos.
- O APFS não diferencia maiúsculas de minúsculas por padrão. Dois arquivos de origem que diferem só pela caixa das letras não coexistem nesse volume; backups feitos no Linux que contenham ambos vão colidir ao serem restaurados em um APFS padrão. O HFS+ também normaliza nomes acentuados para NFD; o APFS preserva a forma original. Sempre que possível, mantenha estado e destino no mesmo tipo de volume da origem.
- Ler `~/Documents`, `~/Desktop`, `~/Downloads`, Mail e pastas semelhantes exige **Acesso Total ao Disco** (Ajustes do Sistema, Privacidade e Segurança). Quando os backups rodam pelo `cron` ou `launchd`, conceda essa permissão ao interpretador Python do ambiente virtual (ou ao `/usr/sbin/cron`); caso contrário o inventário falha com erros de permissão.
- Jobs em lotes rejeitam nomes com caracteres de controle, o que inclui os arquivos `Icon\r` que o Finder cria em pastas com ícone personalizado. Exclua-os com a regra `Icon[\x0d␍]` descrita adiante; jobs simples os copiam pelo rclone.
- Agende com `cron` ou com um agente `launchd` em `~/Library/LaunchAgents`; o comando é o mesmo `.venv/bin/sambinha backup`.

Os testes marcados como `RealMacOSTests` em `tests/test_macos.py` rodam somente em um Mac e conferem esses comportamentos no sistema real; os demais testes de macOS simulam a plataforma e rodam também no CI Linux.

## Faça seu primeiro backup

Comece com uma cópia simples para outra pasta ou disco. Na pasta do projeto, crie um arquivo chamado `sambinha.toml`. Ele é a lista de instruções que o programa vai ler. Este exemplo contém uma única tarefa de backup:

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"

[[jobs]]
name = "meus-documentos"
source = "~/Documentos"
destination = "/mnt/backup/documentos"
type = "local"
logic = "simple"
log_retention_days = 14
deleted_days = 10
version_days = 2
verification_mode = "simple" # Padrão: tamanho em bytes e data de modificação.
```

Troque `source` pela pasta que deseja proteger e `destination` pela pasta onde a cópia será guardada. O destino deve ficar fora da pasta de origem. Se ele estiver em um disco externo, conecte e monte o disco antes de executar. Destinos de jobs diferentes não podem se sobrepor; o destino de um job anterior pode ser origem de outro posterior. Consulte as interações abaixo.

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

## Configure o tamanho dos lotes

Em cada job com `logic = "batch"`, use `max_megabytes` para limitar os dados originais por lote, antes da compressão:

```toml
max_megabytes = 1024 # 1024 MB = 1 GiB; 1 MB = 1.048.576 bytes
```

O padrão é 1024 e decimais são aceitos, como `max_megabytes = 0.5`. Para uma execução, use `--max-megabytes 100`. Configurações antigas devem substituir `max_bytes` por `max_megabytes` e dividir o valor por 1.048.576; `max_bytes = 1073741824` vira `max_megabytes = 1024`. Lotes confirmados mantêm seus membros, mesmo que cresçam acima do limite; veja [lotes e rebalanceamento](batch-lifecycle.pt-BR.md).

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

Para arquivos Icon do Finder/macOS, use `Icon[\x0d␍]`: corresponde ao CR final ou à sua representação visível pelo rclone, preservando nomes como `Icon.png` e `Icon1`. Prefira padrões glob quando possível; regras regex `{{...}}` impedem que o rclone evite diretórios excluídos. A descoberta batch usa um único processo local autenticado do rclone, com prazo `timeout` separado por diretório. As regras são congeladas por job, então edições valem na próxima execução. Veja [descoberta e performance](performance.pt-BR.md#descoberta-da-origem-com-operações-limitadas).

Confira a quantidade de arquivos selecionados e o trabalho previsto com uma simulação:

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
```

Os arquivos excluídos não são enviados e não são tratados como arquivos apagados. Cópias já existentes no destino **não são apagadas só por passarem a ser excluídas**; seus históricos ficam protegidos enquanto a regra existir. Um lote antigo que misture arquivos incluídos e excluídos é mantido à parte, no índice `excluded`; os lotes atuais contêm somente a seleção atual. Cada tarefa pode usar um arquivo de regras diferente. Arquivo ausente, ilegível ou com UTF-8 inválido interrompe o backup. Alterações após o job congelar suas regras valem quando ele começar novamente; jobs posteriores leem suas próprias regras no início. O [guia de uso](usage.pt-BR.md#exclusões-por-arquivo-de-regras) detalha esse comportamento.

## Confirme que os discos estão disponíveis

Para um backup em discos montados, informe na tarefa `source_mount` e `destination_mount`: são os pontos onde os discos aparecem no sistema (`/mnt/...` ou `/media/...` no Linux, `/Volumes/NOME` no macOS), não necessariamente as subpastas copiadas. Por exemplo, um destino `/mnt/capsule/LLLD/linux` usa `destination_mount = "/mnt/capsule/LLLD"`.

O Sambinha aplica a mesma estratégia do seu comando `mounted_nonempty`: o ponto precisa passar em `mountpoint -q` (no macOS, o dispositivo do ponto precisa diferir do diretório pai) e conter pelo menos uma entrada. Se ele estiver ausente, desmontado, vazio ou ilegível, a execução para antes de copiar e envia erro ao Cronitor. A conferência se repete antes das cópias e exclusões. Configure o ponto da origem e, para destinos locais, o do destino; o S3 tem somente a origem local.

Para pastas comuns que não dependem de um disco montado específico, as opções podem ficar ausentes. O [guia de montagem](usage.pt-BR.md#verificação-dos-discos-montados) mostra a configuração completa e como localizar o ponto correto.

## Saiba com antecedência o que será apagado

O alerta `deletion_alert_threshold` vale somente para históricos batch. Quando mais de 1000 arquivos ou versões batch estiverem próximos da exclusão definitiva, o Sambinha envia um evento ao Cronitor e registra os caminhos e datas. O histórico simples de `--backup-dir` não é contado nem limpo pelo Sambinha.

Para histórico batch, o aviso começa dois dias antes do vencimento. Configure `deletion_alert_threshold` dentro de cada `[[jobs]]` para definir o limite independente daquele job.

## Como recuperar um arquivo

Na cópia simples, os arquivos atuais estão no destino com a mesma organização da origem. Você pode copiá-los de volta como faria com arquivos de qualquer outra pasta. Dentro do destino, a pasta oculta `.sambinha` guarda o histórico e os registros do programa; ela não faz parte dos seus documentos atuais.

Na cópia por lotes, você consulta os arquivos de controle para descobrir em qual lote está o documento e depois extrai o arquivo compactado com o 7-Zip. O Sambinha também envia uma cópia compactada do índice de controle para o destino, para ajudar na recuperação se o computador de origem for perdido.

O [guia de configuração e recuperação](usage.pt-BR.md) mostra os caminhos do histórico e os passos para recuperar arquivos e o índice. Ele também explica os limites dos lotes e os cuidados para atualizar uma instalação anterior. Conhecer esses passos antes de precisar deles ajuda a escolher a forma de backup mais conveniente.

## Como testar o programa

Os testes fazem backups de arquivos pequenos, recuperam as cópias e comparam seus caminhos e seu conteúdo com a origem. Isso inclui cópias simples e por lotes, tanto locais quanto no S3. Para o S3, usam o **Moto**, um simulador da AWS que roda no próprio computador: seus dados e suas credenciais reais não são usados.

Prepare as ferramentas de teste no mesmo ambiente do projeto e execute a suíte completa, incluindo o servidor S3 simulado:

```bash
.venv/bin/python -m pip install -e '.[test,dev]'
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m pytest -n auto --cov=sambinha --cov-branch --cov-report=term-missing --cov-fail-under=85
```

`make test`, `make coverage`, `make lint` (ruff) e `make typecheck` (mypy) executam os mesmos comandos; `make test-fast` pula os testes do servidor S3 por HTTP. Os testes continuam sendo casos `unittest`, então `python -m unittest discover` ainda funciona, apenas em série. O `ruff format` está configurado mas não é exigido: a integração contínua executa `ruff check` e mypy em uma etapa de lint antes dos testes (Python 3.12 e 3.13).

O resultado deve terminar com a contagem de testes aprovados. A última linha da tabela de cobertura mostra quanto do código os testes percorreram. As verificações incluem arquivos vazios, nomes com acentos, arquivos editados ou apagados, falhas de envio, recuperação após falhas e simulação. Os arquivos gerados têm no máximo **100 KiB**, e as pastas temporárias são removidas ao final.

O [relatório da versão atual](test-results.pt-BR.md) registra os resultados obtidos. A cada envio de código, o GitHub também executa esses testes automaticamente; você pode acompanhar o resultado na [página de testes do projeto](https://github.com/lesantos/sambinha/actions).

## Para continuar

Todas as opções públicas de configuração estão em [sambinha.example.toml](../sambinha.example.toml), inclusive as opcionais comentadas; um teste confere essa lista com o código. Depois do primeiro backup, o [guia de uso](usage.pt-BR.md) ajuda a ajustar prazos, limites e várias tarefas. O [guia de S3](s3.pt-BR.md) trata da nuvem e das credenciais. Para quem quiser contribuir com o código, a [arquitetura](architecture.pt-BR.md) explica como o projeto está organizado, e as [notas da versão](../CHANGELOG.pt-BR.md) registram as mudanças.

As orientações e os resultados da auditoria de credenciais estão em [SECURITY.md](../SECURITY.pt-BR.md). A CI também verifica o histórico completo para detectar novas credenciais no código.

## Jobs e execuções em paralelo

Os jobs executam sequencialmente, na ordem do TOML. Cada job lê suas exclusões e confere discos/montagens; jobs simples deixam o rclone percorrer e sincronizar as árvores, enquanto jobs batch criam inventário e estado indexado. As opções de concorrência atuam dentro de cada job. Backups e verificações normais obtêm uma trava exclusiva sem espera em `state_dir`: uma segunda execução usando o mesmo diretório de estado termina com erro, antes de alterar dados do backup.

## Retenção dos logs

`log_file_level` e `log_terminal_level` de cada job escolhem independentemente os eventos JSON gravados no arquivo e mostrados no stdout. Ambos aceitam `"error"` (somente erros), `"summary"` (erros, alertas, resumos por job e resultado final) e `"detailed"` (todos os eventos, inclusive por arquivo). O padrão dos dois é `"summary"`. No sync simples, o Sambinha solicita ao rclone apenas o nível `ERROR` por padrão; se qualquer saída estiver em `detailed`, solicita `DEBUG` e encaminha esses registros à saída detalhada. A opção detalhada pode produzir logs grandes. O progresso interativo continua no stderr e recebe apenas as estatísticas periódicas necessárias. Com o rclone 1.60, os avisos de arquivos mais novos preservados por `--update` só aparecem em `DEBUG`; portanto, o padrão preserva esses arquivos sem marcá-los como falha, enquanto `detailed` permite identificá-los e falhar o job para revisão. Para uma execução, use `--log-file-level` e `--log-terminal-level`.

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
log_file_level = "summary"
log_terminal_level = "summary"
```

`log_retention_days = 14` em cada job mantém os logs de auditoria do Sambinha escritos nos últimos 14 dias. Escolha outro número de dias ou `0` para guardar os logs indefinidamente. `--log-retention-days 30` substitui a configuração para uma execução. Backups, restaurações e verificações sem simulação removem apenas arquivos de log regulares e inativos com o formato de nome timestamp/UUID `.jsonl` do Sambinha, diretamente em `log_dir`. A idade conta da última escrita, não da data no nome. O log atual, logs abertos por outras execuções, links simbólicos, subdiretórios e arquivos de outros tipos são preservados. Simulações não removem logs. A limpeza depende das execuções do programa; não instala um timer separado.

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
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

Use `check_destination_disk = false` para suprimir também a checagem de capacidade do destino local. Essa opção inclui a pasta de saída da restauração. Destinos S3 não têm checagem de capacidade local. O espaço temporário da criação de lotes, restauração e downloads de lotes continua sendo verificado; a checagem diária batch não prepara lotes temporários. Essas opções não desativam checagens de montagem, leitura da origem, validação de caminhos ou proteção contra remoções em massa. Falhas reais de escrita, incluindo falta de espaço, continuam causando erro no job. Com as checagens ativadas, capacidade indisponível causa erro; a mensagem indica o caminho e o motivo.

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

Isso vale para backup e verificação manual. O preflight pertence a cada job. Uma falha em um job com `false`, inclusive no preflight, interrompe os próximos jobs sem desfazer o trabalho concluído. Jobs com `true` podem falhar e ser ignorados enquanto os demais jobs válidos executam. Em ambos os casos, qualquer falha deixa o código de saída diferente de zero. Falhas compartilhadas, como ausência do executável rclone ou indisponibilidade da trava do estado, interrompem a execução independentemente dessa opção. Os erros incluem `Job NOME:` na mensagem e o campo `job` no log JSON, inclusive no preflight; as notificações de falha também identificam o job.

## Escolha a verificação simples ou por hash

`verification_mode` e o cache de fingerprints aplicam-se aos inventários batch. Jobs `logic = "simple"` delegam as decisões de sincronização ao rclone: `simple` usa comparação tamanho/data; `hash` passa `--checksum`. A verificação manual usa `rclone check`.

Jobs batch conferem se os arquivos de origem permanecem estáveis durante a leitura e a compactação. Defina `verify_source_changes = false` dentro do job para ignorar essas checagens e deixar as próximas alterações para backups futuros. Com o padrão `true`, um arquivo alterado é retentado até três vezes após a primeira leitura; se continuar mudando, o backup falha. Essa opção não altera o funcionamento de `rclone sync` nos jobs simples.

Para comparar o conteúdo por SHA-256 e MD5, configure:

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
verification_mode = "hash"
```

Também é possível escolher o modo para uma execução:

```bash
.venv/bin/sambinha --config sambinha.toml --verification-mode simple
.venv/bin/sambinha --config sambinha.toml --verification-mode hash
```

No modo hash, o inventário batch lê o conteúdo primeiro e periodicamente depois. Trocar o modo em backups por lotes pode reconstruir arquivos compactados porque os metadados do índice mudam. Lotes e controles de recuperação mantêm checksums nos dois modos.

Durante a execução interativa, o stderr exibe o percentual do backup inteiro, a etapa e o diretório. Jobs simples acompanham as estatísticas do `rclone sync`; fases batch usam contadores ponderados e estimativas limitadas. O percentual não volta nem reinicia e chega a 100% somente após conclusão bem-sucedida. Eventos de cada arquivo aparecem no log JSON ou stdout somente se o respectivo nível for `detailed`. Progresso não entra nos logs JSON nem stdout e fica desativado quando stderr não é um terminal.

## Recursos adicionais

A versão atual é **0.14.9**. A documentação padrão é em inglês; este arquivo e os guias `.pt-BR` mantêm o português do Brasil. Os exemplos públicos também têm versões comentadas nos dois idiomas.

### Restaurar arquivos ou pastas

```bash
.venv/bin/sambinha restore --job documents --path projects --output /mnt/recovery/documents
.venv/bin/sambinha restore --job documents --path notes.md --output /mnt/recovery/notes
.venv/bin/sambinha restore --job documents --output /mnt/recovery/all
```

`--path` aceita repetição para jobs simples ou batch. Em jobs simples, o rclone seleciona arquivos/diretórios atuais do destino; não são versões históricas. Use o rclone para copiar versões antigas de `.sambinha/history/<id-da-execução>`. A pasta de saída deve ser nova ou vazia, separada de origens, backups, estado e logs. Lotes passam por verificação de checksum e integridade do 7-Zip. Para lotes, pode recuperar o índice perdido a partir de `.sambinha/control.tar.gz`.

A restauração batch baixa e extrai em uma pasta temporária dentro de `--output`, usando o disco da restauração em vez do temporário padrão da máquina. Cada lote compactado e seus membros extraídos não selecionados são removidos antes de processar o próximo lote. Arquivos selecionados ficam no disco de saída até todo o conteúdo solicitado passar pela validação. A publicação usa links físicos quando suportados; caso contrário, copia e remove cada arquivo temporário por vez. Reserve espaço para a seleção restaurada mais um lote compactado/descompactado e, em sistemas sem links físicos, um arquivo adicional durante a publicação. As checagens de espaço temporário também medem o disco de saída. A simulação não cria a pasta de saída.

Restaurar do S3 baixa os objetos selecionados e pode gerar custos de recuperação; selecionar um membro de lote baixa o lote inteiro. GLACIER/DEEP_ARCHIVE exigem uma restauração prévia pela AWS.

### Atividades independentes dos lotes

```bash
sambinha verify-local-metadata --job documents-s3         # Diário: tamanho/data da origem contra JSONs locais
sambinha verify-destination-metadata --job documents-s3   # Mensal: sincronia dos JSONs e tamanho dos lotes
sambinha verify-destination-integrity --job documents-s3                # Manual: teste de integridade do 7-Zip
```

Em jobs batch, `verify-local-metadata` usa a origem atual e os JSONs locais, sem ler lotes, calcular hashes ou consultar o S3. Informa `needs_update`, arquivos novos/alterados/removidos e lotes afetados; execute `backup` para aplicar atualizações. Uma checagem concluída retorna 0 mesmo se houver atualizações necessárias. `metadata_dir = "./metadata"` deixa o cache ao lado de state/logs; ausente/vazio preserva `state/JOB/controls`. JSONs inalterados não são reenviados.

`verify-destination-metadata` compara os JSONs completos conhecidos locais/do destino e os metadados de tamanho dos lotes. Controles e índice de recuperação são enviados em STANDARD independentemente da classe dos lotes. Só esses arquivos de metadados são baixados, nunca os lotes. Estado/catálogo local ausente pode ser recuperado pelo índice. Regras de ciclo de vida devem preservar o acesso imediato aos metadados.

`verify-destination-integrity` compara separadamente a listagem e executa `7z t`, sem comparações MD5/SHA nem arquivos extraídos no disco. Lotes locais são lidos diretamente; lotes S3 são baixados/removidos um por vez e Glacier/Deep Archive precisam de restore prévio pela AWS. Datas 7z/ZIP-NTFS preservam 100 ns; ZIPs antigos podem preservar um/dois segundos. Essa perda aceita entra na comparação dos lotes; os JSONs preservam o `mtime_ns` original.

`verify_interval_days` permite a checagem local leve após backups batch bem-sucedidos; 0 desativa. Integridade é manual. `verify-local-metadata` simples continua usando `rclone check`. Veja [atividades e garantias de publicação](batch-checks.pt-BR.md) para migração do cache, agenda, falhas e espaço em disco.

### Proteção contra remoções e monitoramento dos discos

Cada job simples configura `max_delete` (padrão 100), limite absoluto passado ao rclone. Ao excedê-lo, o rclone interrompe a sincronização com erro. Jobs batch continuam usando o bloqueio percentual calculado pelo índice anterior. `--allow-mass-deletion` ignora o limite aplicável naquela execução.

Por padrão, discos da origem e do destino local geram erro com ocupação **acima de 95%**; exatamente 95% é aceito. Espaço indisponível à conta também entra na ocupação. Os discos temporários dos lotes e a saída de restauração são verificados. Um bucket S3 não oferece porcentagem de ocupação de disco local. Há verificações antes do backup e antes de transferências/exclusões, e eventos `disk_usage` registram espaço livre e ocupação.

A criação de lotes também confere o espaço estimado para os maiores lotes simultâneos antes de iniciar os workers e novamente antes de criar cada arquivo compactado. O compressor lê diretamente da origem e valida os metadados/conteúdo configurados antes e depois da leitura. Lotes acima de `max_megabytes` são processados um por vez. Configure `[backup].temp_dir` ao lado de `state_dir`, `log_dir` e `metadata_dir` para usar outro disco; um único diretório existente e gravável será compartilhado por todos os jobs da configuração. Sem essa opção, usa-se o diretório temporário padrão do Python. Veja [espaço temporário e concorrência](performance.pt-BR.md).

Cada tarefa emite `summary`: arquivos novos, modificados, removidos e intactos, lotes reutilizados/recriados, bytes originais previstos, bytes de conteúdo realmente transferidos, histórico pendente e sucesso. Compactação pode mudar a quantidade de bytes enviados; controles e índice não entram nessa medida de conteúdo.

### Performance e perfis de compactação

As opções de performance incluem cache de fingerprints com leitura completa periódica, transferências simples agrupadas, compactação/envio de lotes com concorrência limitada, cache de filtros por job e nível/threads únicos para todos os tipos. Desde 0.9.0 os inventários são independentes por job para observar alterações feitas pelos anteriores. Confira o [guia de performance](performance.pt-BR.md).

```toml
[[jobs]]
name = "media"
source = "~/Files"
destination = "/mnt/backup/media"
logic = "batch"
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2

compression_level = 3
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

No modo hash, o cache exige dispositivo/inode/tamanho/mtime/ctime/permissões iguais, com leitura completa periódica. O modo simples usa tamanho/data. Compressão única (`compression_level`, `compression_threads`) se aplica a todos os tipos e somente a arquivos compactados criados/recriados. Lotes confirmados mantêm membros mesmo ao crescer; veja [planejamento, alertas e rebalanceamento manual](batch-lifecycle.pt-BR.md).

Metadados e logs têm permissões privadas. O índice é validado antes da retenção e restauração; caminhos inseguros, links e conflitos entre dados ativos e histórico são rejeitados. Credenciais conhecidas são ocultadas em erros, logs e telemetria. Consulte [segurança](../SECURITY.pt-BR.md).

## O mesmo backup em várias máquinas

O **nome** do job é a identidade do backup, junto com tipo e lógica (e o destino S3 em jobs S3). Caminhos de origem e de destino local são registrados por máquina como origens informativas, então o mesmo job pode rodar no Linux (`/mnt/disk/Docs`) e no macOS (`/Volumes/disk/Docs`) contra um só destino, em alternância, sem perder o histórico. Mantenha o mesmo nome em `[[jobs]]` em todas as máquinas e dê a cada uma seu próprio `state_dir`; a primeira execução em uma máquina nova adota o índice confirmado do destino, recalcula os hashes da origem uma vez (o cache de fingerprints é por dispositivo) e segue dali. Restaurações funcionam de qualquer máquina com a configuração e as credenciais.

Pontos de montagem por máquina podem ser expressos com `source_mount`/`destination_mount` apontando para o volume daquela máquina, ou com `${VARS}` em `source`/`destination` resolvidas do ambiente de cada máquina. Renomear um job, ou apontar um job para um destino que pertence a outro nome, é recusado (`Destino pertence ao job X`); execute `sambinha --config ... backup --adopt-identity` uma vez para assumir o backup existente; depois disso o novo nome fica gravado no marcador do destino e a opção não é mais necessária. Um estado anterior a 0.16 na mesma máquina migra sozinho; um cujos caminhos diferem também exige `--adopt-identity`. Diferenças de tipo, lógica e destino S3 nunca são adotadas.

## Jobs sequenciais e caminhos sobrepostos

Desde 0.9.0, os jobs executam até o fim na ordem do TOML. Cada job congela seu próprio arquivo de exclusões, confere montagens/discos, faz um inventário atualizado usando somente seu próprio cache persistente, transfere e aplica retenção, confirma o índice, realiza a verificação devida e grava o resumo antes de o próximo começar. Uma falha no preflight posterior não impede a conclusão anterior. `continue_on_job_error` continua pertencendo ao job que falhou; não há ordenação automática de dependências nem reversão.

Destinos não podem se sobrepor entre jobs, inclusive caminhos locais aninhados ou prefixos S3. O destino de um job pode ser a origem de outro job posterior: ele verá os arquivos concluídos pelo anterior, como ao executar comandos separados na mesma ordem. Cada job ainda recusa sobreposição entre sua própria origem e destino, e todas as origens/destinos devem ficar separados de estado/logs.

Confira estas interações ao compartilhar caminhos:

- A cópia simples administra toda a árvore selecionada no destino. Arquivos ausentes na origem daquele job podem ir para o histórico e ser removidos, mesmo quando outro job os criou. Exclua os caminhos do outro job quando precisar preservá-los; subdiretórios separados facilitam a administração.
- Um job posterior pode substituir dados anteriores ou remover lotes durante a retenção. A verificação registra sucesso no instante em que roda; não garante que jobs posteriores preservem os dados. Uma verificação posterior de um índice anterior pode falhar. Proteções de destino mais novo e remoções da origem continuam ativas.
- Jobs por lotes no mesmo destino compartilham `.sambinha/control.tar.gz`. O último escritor substitui o índice remoto de recuperação. Preserve o índice local de cada job; recuperar os anteriores só pelo remoto pode falhar. Lotes compartilhados também podem ser removidos por um job que desconhece o uso por outro.
- `.sambinha` continua reservado na raiz da origem. Copiar um destino inteiro diretamente como outra origem pode falhar depois que históricos ou controles de lotes aparecerem ali. Use um diretório pai para deixar os metadados abaixo da raiz da origem, como no exemplo `LLLD`. Históricos aninhados passam a fazer parte dos dados selecionados, salvo exclusão.
- Simulações não criam arquivos ou origens intermediárias. Um job cuja origem seria criada pela transferência real anterior pode falhar na simulação; comandos separados de simulação se comportam da mesma forma.

As contagens de alertas de exclusão são independentes por job, para que combinar comandos não altere o limite. Jobs com origens iguais agora fazem inventários independentes, o que pode aumentar o IO; os caches persistentes continuam funcionando. O progresso atribui faixas iguais aos jobs porque os tamanhos das origens posteriores só são conhecidos quando eles começam. Dentro de cada job, arquivos/lotes continuam ponderados por bytes.

Uma execução mantém configuração, log, progresso global, ciclo Cronitor e trava compartilhados. Retorna falha se algum job executado falhar. Comandos separados têm logs/notificações separados e liberam a trava entre comandos; um comando combinado mantém a trava durante toda a execução. Erros de leitura/validação da configuração e dependências globais indisponíveis ainda impedem a execução. Esses detalhes não alteram os resultados bem-sucedidos de cópia ou verificação para os mesmos jobs e ordem, sem gravações externas entre eles.


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.
