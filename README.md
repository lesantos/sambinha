# Smart Batch File Backuper — smabafiba

Backup de arquivos em Linux, sem root, para diretório local/montado ou Amazon S3. Python **3.12+**, compatível com o Python dos pacotes do Linux Mint. Usa **rclone** para inventário, comparação por checksum e transferência e **7-Zip** para compactar lotes. Sem dependências Python em execução.

## Funcionalidades

- Cópia simples com caminhos originais e preservação de permissões, datas e atributos locais quando possível.
- Lotes determinísticos que acumulam arquivos e árvores de vários diretórios até os limites de tamanho ou quantidade; arquivos originais não são enviados nessa lógica.
- Controles JSON legíveis com caminhos, tamanhos, SHA-256, MD5, permissões e datas. Espelho local e arquivo de recuperação do índice no destino.
- Vários jobs independentes, inclusive a mesma origem com destinos e lógicas diferentes.
- Arquivos idênticos são registrados e não enviados novamente. Modificações de mesmo tamanho/data são detectadas por conteúdo.
- Retenção de arquivos apagados (10 dias) e versões anteriores (2 dias), configurável separadamente.
- Uma tentativa inicial e **até três retentativas**, com **2 segundos** de intervalo, para operações de transferência. Sem retentativas internas adicionais do rclone.
- Simulação, preflight de dependências/origens, trava contra execuções simultâneas e logs JSONL por execução.
- Eventos Cronitor `run`, `complete` e `fail`, com a mesma identificação de execução.

## Instalação

As dependências do sistema precisam estar instaladas; o programa roda na conta do usuário:

```bash
sudo apt install python3 python3-venv rclone p7zip-full
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/smabafiba --version
```

Também é possível executar diretamente, sem instalação Python:

```bash
python3 -m smabafiba --config smabafiba.toml --dry-run
```

## Configuração e uso

O arquivo local ignorado pelo Git `smabafiba.toml`, quando presente, pode conter a URL privada do Cronitor. Edite origens e destinos antes de executar. Para criar uma configuração nova, copie [smabafiba.example.toml](smabafiba.example.toml) para `smabafiba.toml` e ajuste as origens e destinos. Caminhos relativos são resolvidos a partir do diretório do TOML, inclusive os overrides. `~` e variáveis de ambiente são aceitos nos caminhos.

```bash
cp smabafiba.example.toml smabafiba.toml
# Edite origens, destinos e nome de bucket antes de executar.
python3 -m smabafiba --config smabafiba.toml --dry-run
python3 -m smabafiba --config smabafiba.toml --job documents-local
python3 -m smabafiba --config smabafiba.toml --job documents-s3
```

Todos os parâmetros operacionais têm overrides CLI; opções omitidas herdam o TOML:

```bash
python3 -m smabafiba --job documents-s3 --max-bytes 102400 --max-files 10 --dry-run
python3 -m smabafiba --job documents-local --deleted-days 30 --version-days 7
python3 -m smabafiba --config /caminho/outro.toml --job documents-local --destination /mnt/backup
python3 -m smabafiba --help
```

`--source`, `--destination`, `--type` e `--logic` exigem um único job selecionado. `--no-dry-run` sobrepõe simulação habilitada no TOML. Tipos: `local`, `s3`. Lógicas: `simple`, `batch`. Compactação: `7z` ou `zip`; `compressor` aceita `7z`, `7zz` ou outro executável compatível com a CLI do 7-Zip.

Códigos de saída: **0** sucesso, **1** erro de execução/notificação, **2** configuração inválida, **130** interrupção. Jobs independentes continuam após falha de um job; o resultado global registra falha. Origens e dependências de todos os jobs são verificadas antes da primeira transferência.

## S3 e credenciais fora do Git

O TOML contém o destino `remote:bucket/prefixo`, a classe de armazenamento e a referência `rclone_config`. [rclone.example.conf](rclone.example.conf) completa a conexão: remote `aws`, provider AWS, região e `env_auth=true`. Crie o bucket previamente e ajuste a região para a região real do bucket. Cada job precisa de prefixo exclusivo.

A configuração versionada **não contém chaves AWS**. Com `env_auth=true`, o rclone usa a cadeia de credenciais AWS. Para uma máquina pessoal, configure um perfil fora deste projeto, em `~/.aws/credentials`:

```ini
[smabafiba]
aws_access_key_id = SUA_CHAVE
aws_secret_access_key = SEU_SEGREDO
# Se forem credenciais temporárias:
# aws_session_token = SEU_TOKEN
```

Proteja esse arquivo e selecione o perfil:

```bash
chmod 700 ~/.aws
chmod 600 ~/.aws/credentials
export AWS_PROFILE=smabafiba
python3 -m smabafiba --job documents-s3 --dry-run
```

Se usar AWS CLI, `aws configure --profile smabafiba` cria o perfil de forma interativa; AWS CLI não é uma dependência do aplicativo. Em EC2/ECS, prefira uma **role IAM**, com credenciais temporárias e sem chaves em arquivos. Limite a política IAM ao bucket/prefixo de backup: listar o bucket, ler, enviar e apagar objetos; cópias de histórico precisam de leitura e escrita. Para restaurar Glacier Flexible/Deep Archive, também é necessário `s3:RestoreObject`.

O endpoint Cronitor contém um token. Guarde `CRONITOR_URL` em um arquivo privado fora do repositório, por exemplo `~/.config/smabafiba/secrets.env`, com permissão `600`:

```bash
# Conteúdo do arquivo privado, com o endpoint fornecido por você:
export CRONITOR_URL='https://cronitor.link/p/SEU_TOKEN/SEU_MONITOR'
```

Antes da execução, carregue esse arquivo com `source ~/.config/smabafiba/secrets.env`. O programa lê `CRONITOR_URL` automaticamente se `cronitor_url` estiver ausente no TOML. Sem URL, notificações ficam desabilitadas. A URL nunca aparece nos logs de notificação. `smabafiba.toml`, `.aws/`, estado e logs são ignorados pelo Git; não coloque segredos nos arquivos de exemplo. A [especificação versionada](docs/specification.md) substitui o endpoint privado por uma referência de ambiente.

## Classes S3 e custo

O padrão é **GLACIER_IR**: armazenamento frio com acesso imediato, cobrança por recuperação, duração mínima faturada de 90 dias e tamanho mínimo faturado de 128 KiB por objeto. Controles JSON e `.smabafiba/control.tar.gz` usam sempre **STANDARD**, para consultar e recuperar o índice sem restore.

| Classe | Acesso e características |
| --- | --- |
| STANDARD | Imediato; maior custo de armazenamento; sem duração mínima de arquivo frio |
| STANDARD_IA / ONEZONE_IA | Imediato; cobrança de recuperação e mínimo de 30 dias; ONEZONE usa uma zona |
| INTELLIGENT_TIERING | Transição automática; custos de monitoramento conforme tamanho elegível |
| GLACIER_IR | Frio, acesso imediato; mínimo de 90 dias e cobrança de recuperação |
| GLACIER | Restore assíncrono em minutos/horas; mínimo de 90 dias |
| DEEP_ARCHIVE | Restore em horas; mínimo de 180 dias |

`GLACIER` e `DEEP_ARCHIVE` são aceitos para `batch`. `simple` exige classe acessível sem restore, pois precisa copiar versões antigas antes de substituir o objeto. Retenção de 2/10 dias em classes frias pode gerar cobrança de exclusão antecipada; retenção da aplicação não cancela a duração mínima faturada. Custos variam por região, volume e operações. Veja [classes S3 da AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html) e [backend S3 do rclone](https://rclone.org/s3/).

## Regras dos lotes

1. A ordenação por diretório/caminho é determinística. Arquivos de vários diretórios são acumulados no mesmo lote. Um diretório pode ocupar mais de um lote quando os limites exigirem divisão.
2. `max_bytes` limita a **soma dos tamanhos originais**, antes da compactação; cabeçalhos e dados incompressíveis podem tornar o arquivo compactado maior. `max_files` limita a quantidade de arquivos. Padrões: 1 GiB e 1000 arquivos.
3. Lotes normais ficam no ancestral comum mais próximo dos arquivos, com nome `batch-<hash>.7z`. Os caminhos dentro deles são relativos à raiz da origem, preservando a árvore original.
4. Lotes têm pelo menos dois arquivos quando o conjunto e os limites permitem. Um arquivo maior que o limite, uma origem com um único arquivo ou arquivos que não podem formar um par dentro dos limites são compactados individualmente. Um lote anterior com mais de dois arquivos pode ceder um arquivo para evitar sobra isolada.
5. Arquivos individuais conservam o nome com a extensão substituída, em `<diretório>/.versions/<hash>/<nome>.7z`. Essa revisão evita colisões entre `nome.txt`/`nome.csv` e permite reter versões sem copiar objetos frios.
6. Cada arquivo compactado tem `<nome>.7z.json` ao lado. O espelho em `<state_dir>/<job>/controls/` armazena apenas os controles, sem guardar os lotes compactados.
7. Os lotes são criados em diretórios temporários e apagados inclusive em falhas. Snapshots, teste de integridade do 7-Zip e validação de caminhos/tamanhos do lote precedem o envio. O índice local só é atualizado após enviar os lotes, controles e arquivo de recuperação.
8. Mudanças podem reorganizar lotes na ordenação. Lotes com chave idêntica são reutilizados sem consulta remota; o índice local deve ser preservado e corresponde a um destino administrado exclusivamente pelo job.

## Retenção, recuperação e limites

Na lógica simples, o destino apresenta os caminhos originais. Antes de substituir ou apagar um arquivo, a versão anterior é copiada para `.smabafiba/history/version/<id>/...` ou `.smabafiba/history/deleted/<id>/...`. Na lógica por lotes, arquivos compactados antigos permanecem no caminho original até expirar; o índice separa `active` e `retired`.

A contagem começa quando uma execução bem-sucedida detecta a alteração/remoção. A limpeza ocorre durante execuções seguintes, inclusive no instante exato da expiração. Retenção zero permite apagar o histórico nessa execução. Um lote com arquivos editados e apagados usa o maior prazo aplicável. Falhas interrompem a operação afetada; preservam o último índice confirmado. Uploads interrompidos podem deixar objetos sem referência; não são apagados automaticamente para evitar perda de dados.

Para recuperar `simple`, copie os arquivos atuais do destino, excluindo `.smabafiba/`; consulte o histórico para versões antigas. Para recuperar `batch`:

- Baixe `.smabafiba/control.tar.gz` e extraia `state.json` e `controls/` para um diretório separado.
- Consulte as chaves em `state.json.active` e baixe **somente os lotes ativos**; arquivos `retired` contêm versões anteriores.
- Para classes com restore, solicite a restauração na AWS antes do download.
- Extraia com `7z x arquivo.7z -o/pasta/restaurada`. Os caminhos internos já incluem a árvore a partir da raiz da origem. Compare SHA-256 e tamanho com os controles.
- Para recuperar o estado local, reponha `state.json` e `controls/` em `<state_dir>/<job>/`, usando a mesma origem/destino/lógica da identidade no índice.

`batch` confia no espelho local para evitar chamadas S3 de inventário. Exclusões/corrupção feitas externamente no destino precisam ser corrigidas recuperando/reconstruindo o índice; não apague o estado local. `simple` consulta checksums do destino e repara divergências. Destinos sem MD5 disponível falham explicitamente para impedir comparações apenas por tamanho. O rclone verifica transferências conforme as garantias do backend.

Links simbólicos, arquivos especiais e nomes com quebras de linha são rejeitados com erro explícito. Origens, destinos, estado e logs não podem se sobrepor; jobs não podem compartilhar destinos. `.smabafiba` na raiz da origem é reservado. Atributos locais não exigem troca de proprietário; S3 não oferece permissões POSIX nativas. A simulação cria apenas o log e faz leitura/preflight; não cria lotes/estado, transfere ou apaga arquivos, nem notifica Cronitor.

## Testes

```bash
python3 -m unittest discover -v
# Para incluir Moto e medir cobertura:
python3 -m venv /tmp/smabafiba-test-env
/tmp/smabafiba-test-env/bin/pip install -r requirements-test.txt
/tmp/smabafiba-test-env/bin/python -m coverage run --branch -m unittest discover -v
/tmp/smabafiba-test-env/bin/python -m coverage report --include='smabafiba/*'
# Integração HTTP completa com rclone real e servidor Moto:
./scripts/test-http-s3.sh
```

A suíte usa `unittest` e `unittest.mock`, integra `rclone`/`7z` reais para backup local e usa o emulador existente **Moto** para S3. Na suíte S3 dentro do processo, apenas o transporte externo é substituído, passando pela implementação real de configuração, storage, lotes, retenção e controles. A suíte HTTP executa o `rclone` real contra um servidor Moto em localhost; requer permissão para criar sockets. O GitHub Actions roda ambas as suítes.

As verificações comparam conjuntos completos de caminhos e **cada byte restaurado** com a origem nas quatro combinações de lógica/destino; para lotes, extraem os arquivos e validam SHA-256, tamanho, controles e índice de recuperação. Também verificam arquivos vazios, Unicode, nomes especiais, alterações de mesmo tamanho/data, criação/remoção, retenção nos limites, falhas e retomada, concorrência, simulação e colisões de nomes. Os testes geram arquivos de **até 100 KiB**, usam limites menores para simular arquivos excedentes e limpam os diretórios temporários. Somente pequenas fixtures de texto são versionadas. Nenhum teste envia dados para AWS ou Cronitor reais.

Detalhes da arquitetura: [docs/architecture.md](docs/architecture.md). Resultado da execução neste ambiente: [docs/test-results.md](docs/test-results.md).
