# Ajustando e recuperando seus backups

O [README](../README.md) apresenta a primeira cópia. Este guia detalha os ajustes disponíveis e o caminho para recuperar arquivos. Cada tarefa tem sua própria origem, destino e modo de cópia; escolha nomes que ajudem a distinguir, por exemplo, o disco externo da nuvem.

## Ajustes pela linha de comando

Todos os parâmetros operacionais têm overrides CLI; opções omitidas herdam o TOML:

```bash
.venv/bin/python -m sambinha --job documents-s3 --max-bytes 102400 --max-files 10 --dry-run
.venv/bin/python -m sambinha --job documents-local --deleted-days 30 --version-days 7
.venv/bin/python -m sambinha --config /caminho/outro.toml --job documents-local --destination /mnt/backup
.venv/bin/python -m sambinha --help
```

`--source`, `--destination`, `--type` e `--logic` exigem um único job selecionado. `--no-dry-run` sobrepõe simulação habilitada no TOML. Tipos: `local`, `s3`. Lógicas: `simple`, `batch`. Compactação: `7z` ou `zip`; `compressor` aceita `7z`, `7zz` ou outro executável compatível com a CLI do 7-Zip.

Códigos de saída: **0** sucesso, **1** erro de execução/notificação, **2** configuração inválida, **130** interrupção. Jobs independentes continuam após falha de um job; o resultado global registra falha. Origens e dependências de todos os jobs são verificadas antes da primeira transferência.

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

Na lógica simples, o destino apresenta os caminhos originais. Antes de substituir ou apagar um arquivo, a versão anterior é copiada para `.sambinha/history/version/<id>/...` ou `.sambinha/history/deleted/<id>/...`. Na lógica por lotes, arquivos compactados antigos permanecem no caminho original até expirar; o índice separa `active` e `retired`.

A contagem começa quando uma execução bem-sucedida detecta a alteração/remoção. A limpeza ocorre durante execuções seguintes, inclusive no instante exato da expiração. Retenção zero permite apagar o histórico nessa execução. Um lote com arquivos editados e apagados usa o maior prazo aplicável. Falhas interrompem a operação afetada; preservam o último índice confirmado. Uploads interrompidos podem deixar objetos sem referência; não são apagados automaticamente para evitar perda de dados.

Para recuperar `simple`, copie os arquivos atuais do destino, excluindo `.sambinha/`; consulte o histórico para versões antigas. Para recuperar `batch`:

- Baixe `.sambinha/control.tar.gz` e extraia `state.json` e `controls/` para um diretório separado.
- Consulte as chaves em `state.json.active` e baixe **somente os lotes ativos**; arquivos `retired` contêm versões anteriores.
- Para classes com restore, solicite a restauração na AWS antes do download.
- Extraia com `7z x arquivo.7z -o/pasta/restaurada`. Os caminhos internos já incluem a árvore a partir da raiz da origem. Compare SHA-256 e tamanho com os controles.
- Para recuperar o estado local, reponha `state.json` e `controls/` em `<state_dir>/<job>/`, usando a mesma origem/destino/lógica da identidade no índice.

`batch` confia no espelho local para evitar chamadas S3 de inventário. Exclusões/corrupção feitas externamente no destino precisam ser corrigidas recuperando/reconstruindo o índice; não apague o estado local. `simple` consulta checksums do destino e repara divergências. Destinos sem MD5 disponível falham explicitamente para impedir comparações apenas por tamanho. O rclone verifica transferências conforme as garantias do backend.

Links simbólicos, arquivos especiais e nomes com quebras de linha são rejeitados com erro explícito. Origens, destinos, estado e logs não podem se sobrepor; jobs não podem compartilhar destinos. `.sambinha` na raiz da origem é reservado. Atributos locais não exigem troca de proprietário; S3 não oferece permissões POSIX nativas. A simulação cria apenas o log e faz leitura/preflight; não cria lotes/estado, transfere ou apaga arquivos, nem notifica Cronitor.


## Atualizando para a versão 0.2.0

Esta versão usa o comando `sambinha`, o arquivo `sambinha.toml` e a pasta de metadados `.sambinha`. Atualize os comandos de execução e os agendamentos que você tiver criado. A versão anterior continua disponível nas suas cópias já instaladas até que você a remova.

Para preservar backups feitos por uma instalação anterior, use um novo destino e uma nova pasta de estado para o primeiro backup da versão 0.2.0. Mantenha o destino e o estado anteriores juntos para consultar ou restaurar esse histórico. O programa recusa reutilizar um estado de outro formato de metadados; ele não converte arquivos remotos automaticamente. Esta mudança não exige reenviar dados para testar: comece sempre com `--dry-run`.

## Arquivos mais novos no destino

Antes de modificar uma tarefa, a cópia simples compara as datas de modificação do inventário rclone com as da origem, com precisão de nanossegundos. Uma data mais recente no destino impede todas as alterações daquela tarefa, inclusive quando os bytes são iguais; o arquivo e sua data permanecem intactos. Datas iguais permitem comparar o conteúdo por checksum. Se o destino não fornecer uma data válida, a tarefa também falha antes de alterar os arquivos.

Nos lotes, a comparação considera as datas originais dos arquivos no último índice confirmado. A data de upload dos lotes não é a data de edição dos documentos. Conflitos aparecem como `newer_destination` nos logs, e a execução termina com código 1 e um evento Cronitor `fail` com a descrição dos arquivos. A simulação registra o conflito e retorna erro, sem enviar notificações.

Nas transferências simples, o comando `copyto` também recebe `--update`. Se o rclone pular um arquivo porque o destino foi atualizado durante a execução, o Sambinha confirma a data novamente e transforma esse conflito em erro, sem alterar o arquivo mais novo.
