# Arquitetura

[English](architecture.md)


`config` valida TOML e overrides antes de executar; `cli` mantém os códigos de saída. `inventory` lê arquivos de forma incremental e detecta alterações durante a leitura. `batches` é planejamento puro, sem IO externo. `storage` traduz operações para rclone local/S3; `process` controla timeouts, dependências e retentativas sem shell. `state` faz gravação atômica e trava de escritor único. `engine` coordena preflight, estratégias e retenção; `logging` e `notify` isolam auditoria e telemetria.

`secrets` usa o parser do [python-dotenv](https://github.com/theskumar/python-dotenv) para validar o arquivo completo como dados. Ele combina os valores com o ambiente herdado, que tem prioridade, sem executar shell ou alterar `os.environ`. `config` resolve referências de variáveis usando essa combinação antes de validar o TOML; `process` passa o mesmo ambiente aos subprocessos. O campo interno `environment` não aceita configuração via TOML e não aparece na representação da configuração.

As estratégias são registradas em `engine.STRATEGIES`; uma nova lógica recebe `Context` com inventário, configuração, estado, storage e log. Novos destinos podem implementar o contrato de `Storage`: inventário, envio, preservação e exclusão. Atualmente `config.validate` lista os tipos/lógicas permitidos e deve ser ampliado junto com a nova implementação.

Jobs simples delegam travessia, exclusões e sincronização a um `rclone sync`. Um `--backup-dir` único em `.sambinha/history/` guarda arquivos substituídos/apagados; `max_delete` é limite absoluto por job e o sync inteiro não é repetido. Nos jobs batch, chaves imutáveis derivadas dos metadados permitem reusar lotes; o índice de recuperação é enviado antes do commit local e a limpeza usa `index_pending` para novas tentativas.

A trava cobre toda a execução. Cada job conclui exclusões, preflight, inventário, transferências, retenção, estado e verificação agendada antes do início do próximo. Inventários são snapshots de uma leitura; sem snapshot de filesystem, modificações concorrentes podem interromper o backup e requerer nova execução. O aplicativo não fornece transação distribuída entre jobs; cada job mantém seu próprio índice. Jobs sobrepostos podem invalidar índices anteriores ou substituir o controle remoto de recuperação compartilhado; conserve o espelho local e prefira prefixos dedicados.

`protection` compara datas com precisão de nanossegundos nos jobs batch. Jobs simples passam `--update` ao rclone para preservar arquivos que ele considera mais novos no destino.

`filtering` congela o arquivo de regras durante cada execução e usa o próprio rclone para avaliar padrões. Nos jobs batch com exclusões, a descoberta da origem chama `lsjson` sem recursão em cada diretório e aplica as regras aos caminhos relativos à raiz; o inventário batch ainda é montado globalmente. Nomes históricos são avaliados em uma árvore temporária de arquivos vazios, incluindo substituições antigas de arquivo por diretório. Lotes mistos protegidos por exclusões ficam em `state.excluded` e não são confundidos com os lotes atuais.

`retention` grava prazos e caminhos originais e agrega previsões de todas as tarefas. O alerta Cronitor é consultivo: muda a telemetria e os logs, mas não o prazo, a limpeza ou o código de sucesso. Falhas reais continuam retornando erro. `mounts` aplica a estratégia de ponto montado e não vazio aos volumes configurados, antes do backup e das operações que alteram o destino.

## Extensões de 0.4.0 a 0.6.1

`recovery` restaura árvores simples atuais pelo rclone e caminhos batch pelo índice ativo, validando checksums e membros seguros. Verificação simples chama `rclone check`; batch valida arquivos compactados e controles. Verificações batch no S3 consultam somente metadados.

`disks` recusa ocupação local acima de 95%. `profiles` define níveis e threads separados para textos, vídeos, fotos e outros. `inventory` usa tamanho/mtime sem ler conteúdo no modo simples padrão; no modo hash, reaproveita fingerprints validados por assinatura de metadados com leituras completas periódicas. Cada job tem inventário próprio, mesmo com origem/seleção iguais, para observar alterações feitas pelos anteriores. `filtering` reaproveita seleções imutáveis só naquela execução.

`progress` exibe etapa/diretório/percentual com limitação de frequência só no stderr interativo. Syncs simples usam as estatísticas em tempo real do rclone; fases batch usam contadores e estimativas ponderadas. `logging` limpa a linha transitória antes de imprimir JSON; progresso não tem evento de auditoria.

Workers de lotes têm staging e relatórios de subprocesso independentes. A thread principal confirma o índice apenas após todos os uploads. `max_delete_percent` protege remoções batch pelo índice anterior; `max_delete` controla o limite absoluto do rclone nos jobs simples.

A trava cobre backup e verificação manual. Restauração lê um snapshot atômico do índice; um lote aposentado simultaneamente pode ficar indisponível e causar erro. Permissões privadas e validação não autenticam índices contra atacante com a mesma conta/root. Somente sucesso da verificação agendada atualiza sua data. Identificadores de código/configuração continuam estáveis; documentação tem versões EN e pt-BR.
