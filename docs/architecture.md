# Arquitetura

`config` valida TOML e overrides antes de executar; `cli` mantém os códigos de saída. `inventory` lê arquivos de forma incremental e detecta alterações durante a leitura. `batches` é planejamento puro, sem IO externo. `storage` traduz operações para rclone local/S3; `process` controla timeouts, dependências e retentativas sem shell. `state` faz gravação atômica e trava de escritor único. `engine` coordena preflight, estratégias e retenção; `logging` e `notify` isolam auditoria e telemetria.

As estratégias são registradas em `engine.STRATEGIES`; uma nova lógica recebe `Context` com inventário, configuração, estado, storage e log. Novos destinos podem implementar o contrato de `Storage`: inventário, envio, preservação e exclusão. Atualmente `config.validate` lista os tipos/lógicas permitidos e deve ser ampliado junto com a nova implementação.

Na cópia simples, cada preservação de histórico é confirmada em estado antes de modificar o arquivo atual. Nos lotes, chaves imutáveis derivadas dos metadados permitem reusar lotes e manter histórico sem movimentar conteúdo frio. O índice de recuperação é enviado antes do commit local; falhas de upload preservam o índice anterior. A limpeza de retenção é registrada no índice local e republicada no arquivo remoto, com marca de nova tentativa se essa publicação falhar.

A trava cobre toda a execução. Preflight verifica todos os diretórios antes de iniciar qualquer transferência. Inventários são snapshots de uma leitura; sem snapshot de filesystem, modificações concorrentes podem interromper o backup e requerer nova execução. O aplicativo não fornece transação distribuída entre jobs; cada job mantém seu próprio índice. O contrato de lotes pressupõe controle exclusivo do prefixo remoto e conservação do espelho local.
