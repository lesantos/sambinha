# Sambinha

[English](README.md)

O Sambinha **0.9.0** faz backup para uma pasta local, disco montado ou Amazon S3. Mantém versões anteriores e registra cada execução. Requer Linux, Python 3.12+, rclone e 7-Zip.

Os jobs executam até o fim na ordem do TOML, com inventários e verificações independentes. Sobreposições entre origens/destinos de jobs diferentes são permitidas; confira as [regras de interação](docs/configuration.pt-BR.md#jobs-sequenciais-e-caminhos-sobrepostos) antes de compartilhar caminhos.

## Instalar

```bash
sudo apt install git python3 python3-venv rclone p7zip-full
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
```

O repositório é privado. Pacotes de instalação também estão disponíveis nas [releases](https://github.com/lesantos/sambinha/releases).

## Configurar e executar

Crie `sambinha.toml`. Os caminhos são relativos a esse arquivo; `~` e variáveis de ambiente são aceitos. Mantenha destino, estado e logs fora da origem e preserve o diretório de estado.

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
log_retention_days = 14
deleted_days = 10
version_days = 2
verification_mode = "simple"

[[jobs]]
name = "documents"
source = "~/Documentos"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
check_source_disk = true
check_destination_disk = true
continue_on_job_error = true
# Arquivo UTF-8 opcional com padrões de exclusão do rclone:
# exclude_files = "./excludes.txt"
```

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
.venv/bin/sambinha --config sambinha.toml
.venv/bin/sambinha verify --config sambinha.toml --job documents
.venv/bin/sambinha restore --config sambinha.toml --job documents --output /mnt/recovered
```

A simulação lê inventários e escreve um log, sem alterar backups/estado ou enviar notificações. A restauração usa uma pasta separada, nova ou vazia. O [exemplo completo](sambinha.example.pt-BR.toml) inclui todas as opções.

## Comportamento essencial

- **Armazenamento e verificação:** `logic = "simple"` preserva a árvore; `"batch"` cria arquivos compactados. O padrão `verification_mode = "simple"` compara tamanho em bytes e data de alteração em nanossegundos; `"hash"` confere conteúdo.
- **Links simbólicos são sempre ignorados:** não são copiados, seguidos ou traduzidos, e diretórios ligados não são percorridos. Cópias e históricos existentes desses caminhos ficam protegidos enquanto o link existir. [Decisão e limites](docs/usage.pt-BR.md#links-simbólicos).
- **Retenção:** arquivos apagados ficam guardados por 10 dias e versões anteriores por 2 dias, por padrão. Logs mantêm 14 dias; `log_retention_days = 0` desativa a limpeza.
- **Jobs:** executam sequencialmente. Cada job tem `continue_on_job_error = true` por padrão; use `false` para interromper quando esse job falhar. Qualquer falha deixa o código de saída diferente de zero, e os erros identificam o job.
- **Discos:** verificações de origem/destino local têm padrão `true` e geram erro acima de 95% de ocupação. Use `false` na respectiva opção por job para suprimir a checagem, como `check_source_disk = false` no Seafile/FUSE. Temporários continuam sendo verificados.
- **Proteções e progresso:** remoções anormais são bloqueadas por padrão. Backups/verificações normais simultâneos com o mesmo `state_dir` são recusados. O progresso começa com o percentual estimado do backup inteiro e mostra etapa/subdiretório; detalhes por arquivo ficam nos logs JSON.

## Mais informações

- [Configuração completa, política de falha, exclusões e retenção de logs](docs/configuration.pt-BR.md)
- [Restauração, verificação, retenção, discos/montagens e limites](docs/usage.pt-BR.md)
- [Performance e perfis de compactação/threads](docs/performance.pt-BR.md)
- [Configuração S3 e classes de armazenamento](docs/s3.pt-BR.md) · [Credenciais e segurança](SECURITY.pt-BR.md)
- [Histórico de versões](CHANGELOG.pt-BR.md) · [Testes](docs/test-results.pt-BR.md) · [Arquitetura](docs/architecture.pt-BR.md)
