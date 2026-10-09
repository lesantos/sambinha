# Novidades do Sambinha

## Próxima versão

O Cronitor avisa sobre exclusões definitivas acima de `deletion_alert_threshold`, sem bloquear o backup. Os logs mostram os arquivos e prazos; a janela é de dois dias (um para retenção de um dia).

`exclude_files` permite ignorar caminhos pelos padrões nativos do rclone. Arquivos excluídos e lotes/históricos que os contêm são preservados, inclusive após mudar a seleção.

Arquivos mais novos no destino são preservados. A tarefa falha antes de alterar os dados, registra os conflitos e envia a descrição ao Cronitor. A proteção cobre cópia simples e datas originais confirmadas no índice de lotes.

O parâmetro opcional `secrets_file` permite carregar credenciais de um arquivo `.env` antes de iniciar o backup. Variáveis já definidas no terminal têm prioridade. Arquivos ausentes ou inválidos interrompem a execução antes de qualquer backup, com mensagens sem valores privados. A opção `--secrets-file` permite substituir ou desativar essa leitura em uma execução.

A leitura usa a biblioteca python-dotenv, instalada junto com o aplicativo, e mantém as variáveis isoladas na configuração e nos subprocessos. Configurações anteriores, sem `secrets_file`, continuam funcionando.

## 0.2.0

O projeto passa a se chamar Sambinha. O pacote Python, o comando, as configurações de exemplo, os registros internos e os testes usam o novo nome. O repositório e a versão publicada no GitHub também passam a usar esse nome.

O README foi reescrito para acompanhar o leitor desde a motivação do programa até o primeiro backup, a recuperação dos arquivos e os testes. Os detalhes de configuração e armazenamento na nuvem ficam em guias próprios.

As pastas de controle no destino passam a usar `.sambinha`. Para atualizar backups existentes com segurança, consulte a [orientação de atualização](docs/usage.md#atualizando-para-a-versão-020).

A CI mantém ações com Node.js 24 e verifica backups locais e S3 simulados, cobertura de código e integridade dos arquivos recuperados.
