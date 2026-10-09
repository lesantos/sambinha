# Novidades do Sambinha

[English](CHANGELOG.md)


## 0.6.0

Documentação padrão em inglês com versões em português do Brasil de README, changelog, segurança, guias e exemplos públicos comentados. Links recíprocos e testes de equivalência dos exemplos. Versão final 0.6.0.

## 0.5.0

P1–P6 em ordem: cache de fingerprints com leitura completa periódica; cópias simples agrupadas pelo rclone; inventários compartilhados; compactação/envio de lotes com concorrência limitada; cache de filtros por execução; níveis e threads separados para texto, vídeo, foto e outros. Lotes intactos continuam reutilizados.

## 0.4.1

Validação de índices, controles e caminhos; separação entre ativo/histórico; metadados e logs privados; recusa de links nos metadados/lock; ocultação ampliada de segredos; restauração com validação de arquivos compactados e criação exclusiva na saída.

## 0.4.0

Comandos `restore` e `verify`, verificação periódica, bloqueio de remoções em massa e resumos por tarefa. Discos locais geram erro acima de 95%. Verificação S3 confere tamanho/MD5 por metadados, sem baixar objetos ou índices ausentes; lotes antigos sem checksum de envio geram erro explícito. Restaurar conteúdo do S3 faz downloads.

## 0.3.1

Novos lotes agrupam os arquivos de cada diretório da data de modificação mais antiga para a mais recente, com desempate pelo caminho. Lotes existentes intactos são preservados com suas chaves e ordem originais, inclusive os criados em versões anteriores. Só arquivos novos e membros de lotes afetados são reagrupados, respeitando os limites e o formato configurados.

## 0.3.0

`source_mount` e `destination_mount` permitem verificar volumes montados e não vazios antes de copiar ou excluir. Problemas de montagem interrompem o backup e enviam erro ao Cronitor.

O Cronitor avisa sobre exclusões definitivas acima de `deletion_alert_threshold`, sem bloquear o backup. Os logs mostram os arquivos e prazos; a janela é de dois dias (um para retenção de um dia).

`exclude_files` permite ignorar caminhos pelos padrões nativos do rclone. Arquivos excluídos e lotes/históricos que os contêm são preservados, inclusive após mudar a seleção.

Arquivos mais novos no destino são preservados. A tarefa falha antes de alterar os dados, registra os conflitos e envia a descrição ao Cronitor. A proteção cobre cópia simples e datas originais confirmadas no índice de lotes.

O parâmetro opcional `secrets_file` permite carregar credenciais de um arquivo `.env` antes de iniciar o backup. Variáveis já definidas no terminal têm prioridade. Arquivos ausentes ou inválidos interrompem a execução antes de qualquer backup, com mensagens sem valores privados. A opção `--secrets-file` permite substituir ou desativar essa leitura em uma execução.

A leitura usa a biblioteca python-dotenv, instalada junto com o aplicativo, e mantém as variáveis isoladas na configuração e nos subprocessos. Configurações anteriores, sem `secrets_file`, continuam funcionando.

Os exemplos mostram todas as opções públicas de configuração, inclusive as opcionais e o caminho do arquivo de credenciais. A CI também confere o histórico completo do Git com Gitleaks para evitar a publicação de segredos.

## 0.2.0

O projeto passa a se chamar Sambinha. O pacote Python, o comando, as configurações de exemplo, os registros internos e os testes usam o novo nome. O repositório e a versão publicada no GitHub também passam a usar esse nome.

O README foi reescrito para acompanhar o leitor desde a motivação do programa até o primeiro backup, a recuperação dos arquivos e os testes. Os detalhes de configuração e armazenamento na nuvem ficam em guias próprios.

As pastas de controle no destino passam a usar `.sambinha`. Para atualizar backups existentes com segurança, consulte a [orientação de atualização](docs/usage.pt-BR.md#atualizando-para-a-versão-020).

A CI mantém ações com Node.js 24 e verifica backups locais e S3 simulados, cobertura de código e integridade dos arquivos recuperados.
