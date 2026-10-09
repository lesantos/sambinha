# Arquitetura

`config` valida TOML e overrides antes de executar; `cli` mantém os códigos de saída. `inventory` lê arquivos de forma incremental e detecta alterações durante a leitura. `batches` é planejamento puro, sem IO externo. `storage` traduz operações para rclone local/S3; `process` controla timeouts, dependências e retentativas sem shell. `state` faz gravação atômica e trava de escritor único. `engine` coordena preflight, estratégias e retenção; `logging` e `notify` isolam auditoria e telemetria.

`secrets` usa o parser do [python-dotenv](https://github.com/theskumar/python-dotenv) para validar o arquivo completo como dados. Ele combina os valores com o ambiente herdado, que tem prioridade, sem executar shell ou alterar `os.environ`. `config` resolve referências de variáveis usando essa combinação antes de validar o TOML; `process` passa o mesmo ambiente aos subprocessos. O campo interno `environment` não aceita configuração via TOML e não aparece na representação da configuração.

As estratégias são registradas em `engine.STRATEGIES`; uma nova lógica recebe `Context` com inventário, configuração, estado, storage e log. Novos destinos podem implementar o contrato de `Storage`: inventário, envio, preservação e exclusão. Atualmente `config.validate` lista os tipos/lógicas permitidos e deve ser ampliado junto com a nova implementação.

Na cópia simples, cada preservação de histórico é confirmada em estado antes de modificar o arquivo atual. Nos lotes, chaves imutáveis derivadas dos metadados permitem reusar lotes e manter histórico sem movimentar conteúdo frio. O índice de recuperação é enviado antes do commit local; falhas de upload preservam o índice anterior. A limpeza de retenção é registrada no índice local e republicada no arquivo remoto, com marca de nova tentativa se essa publicação falhar.

A trava cobre toda a execução. Preflight verifica todos os diretórios antes de iniciar qualquer transferência. Inventários são snapshots de uma leitura; sem snapshot de filesystem, modificações concorrentes podem interromper o backup e requerer nova execução. O aplicativo não fornece transação distribuída entre jobs; cada job mantém seu próprio índice. O contrato de lotes pressupõe controle exclusivo do prefixo remoto e conservação do espelho local.

`protection` compara datas com precisão de nanossegundos antes de alterar uma tarefa. A cópia simples também usa `--update` no rclone e confirma um conflito se ele pular um arquivo atualizado durante a transferência. Nos lotes, a comparação usa os metadados originais do índice confirmado.

`filtering` congela o arquivo de regras durante cada execução e usa o próprio rclone para avaliar padrões. Nomes históricos são avaliados em uma árvore temporária de arquivos vazios, incluindo substituições antigas de arquivo por diretório. Lotes mistos protegidos por exclusões ficam em `state.excluded` e não são confundidos com os lotes atuais.

`retention` grava prazos e caminhos originais e agrega previsões de todas as tarefas. O alerta Cronitor é consultivo: muda a telemetria e os logs, mas não o prazo, a limpeza ou o código de sucesso. Falhas reais continuam retornando erro. `mounts` aplica a estratégia de ponto montado e não vazio aos volumes configurados, antes do backup e das operações que alteram o destino.
