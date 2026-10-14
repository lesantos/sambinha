# Sambinha

[English](README.md)

O Sambinha **0.10.2** faz backup para uma pasta local, disco montado ou Amazon S3. Mantém versões anteriores e registra cada execução. Requer Linux, Python 3.12+, rclone e 7-Zip.

Os jobs executam até o fim na ordem do TOML. Destinos não podem se sobrepor; o destino de um job pode ser a origem de outro job posterior. Confira as [regras de interação](docs/configuration.pt-BR.md#jobs-sequenciais-e-caminhos-sobrepostos).

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
verification_mode = "simple"

[[jobs]]
name = "documents"
source = "~/Documentos"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
max_delete = 100 # Limite absoluto deste job, passado ao rclone sync.
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

A simulação executa `rclone sync --dry-run` e grava um log sem alterar os dados do backup. Jobs simples não criam mais o índice de arquivos do Sambinha; `max_delete` é um limite absoluto por job passado ao rclone. O histórico de `--backup-dir` expira pelo maior valor entre `deleted_days` e `version_days`; como o rclone não identifica se o item foi apagado ou substituído, ambos usam essa retenção conservadora. A restauração aceita `--path` para arquivos e diretórios. O [exemplo completo](sambinha.example.pt-BR.toml) inclui todas as opções.

## Comportamento essencial

- **Armazenamento e verificação:** `logic = "simple"` delega a sincronização da árvore e as exclusões ao rclone; `"batch"` cria arquivos compactados com índice. `sambinha verify` usa `rclone check` nos jobs simples; jobs batch mantêm a verificação pelo índice.
- **Links simbólicos são sempre ignorados:** não são copiados, seguidos ou traduzidos, e diretórios ligados não são percorridos. Em jobs simples, um arquivo no destino que corresponda a um link na origem é tratado como removido e movido para o histórico com prazo; jobs batch protegem caminhos indexados enquanto houver link. [Decisão e limites](docs/usage.pt-BR.md#links-simbólicos).
- **Retenção:** históricos dos jobs simples em `.sambinha/history/<id-da-execução>` expiram pelo maior valor entre `deleted_days` e `version_days`; jobs batch mantêm prazos separados para exclusões e versões. Logs mantêm 14 dias; `log_retention_days = 0` desativa a limpeza.
- **Jobs:** executam sequencialmente. Cada job tem `continue_on_job_error = true` por padrão; use `false` para interromper quando esse job falhar. Qualquer falha deixa o código de saída diferente de zero, e os erros identificam o job.
- **Discos:** verificações de origem/destino local têm padrão `true` e geram erro acima de 95% de ocupação. Use `false` na respectiva opção por job para suprimir a checagem, como `check_source_disk = false` no Seafile/FUSE. Temporários continuam sendo verificados.
- **Proteções e progresso:** cada job simples tem um limite absoluto `max_delete`; `--allow-mass-deletion` o ignora em uma execução. As exclusões nativas do rclone preservam os caminhos excluídos. Backups/verificações normais simultâneos com o mesmo `state_dir` são recusados. O progresso acompanha as estatísticas do `rclone sync`.
- **Detalhamento dos logs:** arquivo e terminal mostram erros e resumos por padrão. `log_file_level` e `log_terminal_level` aceitam `error`, `summary` ou `detailed`. O sync simples solicita somente erros ao rclone por padrão. No rclone 1.60, `--update` continua preservando arquivos mais novos no destino, mas identificá-los e falhar por esses conflitos exige `detailed` em pelo menos uma saída.

## Mais informações

- [Configuração completa, política de falha, exclusões e retenção de logs](docs/configuration.pt-BR.md)
- [Restauração, verificação, retenção, discos/montagens e limites](docs/usage.pt-BR.md)
- [Performance e perfis de compactação/threads](docs/performance.pt-BR.md)
- [Configuração S3 e classes de armazenamento](docs/s3.pt-BR.md) · [Credenciais e segurança](SECURITY.pt-BR.md)
- [Histórico de versões](CHANGELOG.pt-BR.md) · [Testes](docs/test-results.pt-BR.md) · [Arquitetura](docs/architecture.pt-BR.md)
