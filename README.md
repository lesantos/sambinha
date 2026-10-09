# Sambinha

O Sambinha guarda cópias dos seus arquivos em outra pasta, em um disco externo ou no Amazon S3, um serviço de armazenamento na nuvem. Você escolhe o que quer proteger e onde quer guardar a cópia; o programa cuida de comparar os arquivos, enviar o que mudou e manter um histórico por algum tempo.

## Por que ele existe

Fotos, documentos e projetos costumam ficar espalhados em várias pastas. Fazer uma cópia manual de tudo funciona por um tempo, mas é fácil esquecer uma pasta, gastar espaço com cópias repetidas ou perceber tarde demais que um arquivo importante foi apagado.

O Sambinha foi criado para tornar esse cuidado repetível. Ele registra o que fez em cada execução e evita enviar novamente arquivos que já estão iguais no destino. Se você editar ou apagar algo por engano, as versões anteriores podem continuar disponíveis durante o prazo que você escolheu.

Há também uma preocupação com o custo da nuvem. Guardar milhares de arquivos pequenos separadamente pode ser pouco eficiente no S3. Por isso, o Sambinha pode reuni-los em arquivos compactados, chamados **lotes**, mantendo os caminhos das pastas dentro deles. É como organizar vários documentos em caixas identificadas: você consulta a lista do conteúdo antes de abrir uma caixa.

## Como ele protege seus arquivos

Você pode escolher entre duas maneiras de fazer a cópia. A **cópia simples** deixa cada arquivo no destino com seu nome e sua pasta, o que facilita abrir ou recuperar um documento específico. A **cópia por lotes** reúne arquivos de várias pastas e os compacta com o 7-Zip. Ela é útil quando há muitos arquivos pequenos e você quer reduzir a quantidade de objetos guardados na nuvem.

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

A versão atual é **0.2.0**. O repositório é privado, então sua conta precisa ter acesso para baixá-lo. Você também pode baixar o código ou o arquivo de instalação na [página de versões](https://github.com/lesantos/sambinha/releases).

## Faça seu primeiro backup

Comece com uma cópia simples para outra pasta ou disco. Na pasta do projeto, crie um arquivo chamado `sambinha.toml`. Ele é a lista de instruções que o programa vai ler. Este exemplo contém uma única tarefa de backup:

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
deleted_days = 10
version_days = 2

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

Quando o backup local estiver funcionando, você pode adicionar outra tarefa para o S3. O [arquivo de exemplo](sambinha.example.toml) mostra uma mesma origem protegida localmente e na nuvem. Cada tarefa recebe um nome, e você pode executar somente uma delas com `--job NOME`.

Para o S3, você precisará de uma conta AWS, de um **bucket** — o espaço onde os arquivos serão guardados — e de uma configuração do rclone que indique a região e como acessar essa conta. O [guia de S3 e credenciais](docs/s3.md) conduz essa preparação e mostra como manter senhas e tokens fora do Git. O arquivo `sambinha.toml` fica fora dos arquivos versionados, mas os exemplos públicos também devem permanecer sem segredos.

O padrão de armazenamento é `GLACIER_IR`, uma modalidade para dados pouco acessados, com recuperação imediata. Ela pode cobrar pela recuperação e pela exclusão antes do prazo mínimo de armazenamento. As modalidades mais frias exigem uma etapa de restauração na AWS antes de baixar os arquivos. O guia explica essas diferenças para que você escolha a opção adequada, em vez de considerar apenas o preço para guardar os dados.

## Deixe as credenciais em um arquivo privado

Para não precisar carregar as variáveis no terminal toda vez, você pode indicar um arquivo de credenciais no `sambinha.toml`. Acrescente estas linhas à seção `[backup]` que já existe:

```toml
secrets_file = "~/.config/sambinha/secrets.env"
cronitor_url = "${CRONITOR_URL}"
```

Crie esse arquivo fora da pasta do projeto. Ele pode conter o nome de um perfil AWS que você já configurou e a URL do Cronitor:

```bash
export AWS_PROFILE='sambinha'
export CRONITOR_URL='https://cronitor.link/p/SEU_TOKEN/SEU_MONITOR'
```

Proteja o arquivo com `chmod 600 ~/.config/sambinha/secrets.env`, para que apenas sua conta possa lê-lo e alterá-lo. O [guia de credenciais](docs/s3.md#arquivo-privado-de-variáveis) também mostra como usar chaves AWS diretamente, caso você não tenha um perfil configurado.

Depois disso, execute o comando de backup normalmente. O Sambinha lê o arquivo antes de interpretar os caminhos e iniciar o rclone; você não precisa executar `source`. Se uma variável já estiver definida no terminal, ela tem prioridade. Se o arquivo indicado não existir ou contiver um erro, o programa para antes do backup e informa o problema sem mostrar os valores privados.

O arquivo é lido como dados, sem executar comandos. Você pode usar aspas, comentários e a palavra opcional `export`. Os valores nele são literais; para referenciar uma variável na configuração, use `${NOME}` no TOML, como no exemplo do Cronitor. Caminhos relativos de `secrets_file` partem da pasta do TOML. A opção `--secrets-file CAMINHO` permite escolher outro arquivo naquela execução.

## Como recuperar um arquivo

Na cópia simples, os arquivos atuais estão no destino com a mesma organização da origem. Você pode copiá-los de volta como faria com arquivos de qualquer outra pasta. Dentro do destino, a pasta oculta `.sambinha` guarda o histórico e os registros do programa; ela não faz parte dos seus documentos atuais.

Na cópia por lotes, você consulta os arquivos de controle para descobrir em qual lote está o documento e depois extrai o arquivo compactado com o 7-Zip. O Sambinha também envia uma cópia compactada do índice de controle para o destino, para ajudar na recuperação se o computador de origem for perdido.

O [guia de configuração e recuperação](docs/usage.md) mostra os caminhos do histórico e os passos para recuperar arquivos e o índice. Ele também explica os limites dos lotes e os cuidados para atualizar uma instalação anterior. Conhecer esses passos antes de precisar deles ajuda a escolher a forma de backup mais conveniente.

## Como testar o programa

Os testes fazem backups de arquivos pequenos, recuperam as cópias e comparam seus caminhos e seu conteúdo com a origem. Isso inclui cópias simples e por lotes, tanto locais quanto no S3. Para o S3, usam o **Moto**, um simulador da AWS que roda no próprio computador: seus dados e suas credenciais reais não são usados.

Prepare as ferramentas de teste no mesmo ambiente do projeto e execute a suíte completa, incluindo o servidor S3 simulado:

```bash
.venv/bin/python -m pip install -r requirements-test.txt
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*'
```

O resultado deve terminar em `OK`. A última linha da tabela de cobertura mostra quanto do código os testes percorreram. As verificações incluem arquivos vazios, nomes com acentos, arquivos editados ou apagados, falhas de envio, recuperação após falhas e simulação. Os arquivos gerados têm no máximo **100 KiB**, e as pastas temporárias são removidas ao final.

O [relatório da versão atual](docs/test-results.md) registra os resultados obtidos. A cada envio de código, o GitHub também executa esses testes automaticamente; você pode acompanhar o resultado na [página de testes do projeto](https://github.com/lesantos/sambinha/actions).

## Para continuar

Depois do primeiro backup, o [guia de uso](docs/usage.md) ajuda a ajustar prazos, limites e várias tarefas. O [guia de S3](docs/s3.md) trata da nuvem e das credenciais. Para quem quiser contribuir com o código, a [arquitetura](docs/architecture.md) explica como o projeto está organizado, e as [notas da versão](CHANGELOG.md) registram as mudanças.
