# Sambinha

[English](README.md)

O Sambinha **0.18.3** faz backup para uma pasta local, disco montado ou Amazon S3. Mantém versões anteriores e registra cada execução. Requer Linux ou macOS, Python 3.12+, rclone 1.60+ e 7-Zip 22.00+.

Os jobs executam até o fim na ordem do TOML. Destinos não podem se sobrepor; o destino de um job pode ser a origem de outro job posterior. Confira as [regras de interação](docs/configuration.pt-BR.md#jobs-sequenciais-e-caminhos-sobrepostos).

## Instalar

São necessários **rclone 1.60 ou mais novo** e **7-Zip 22.00 ou mais novo** (`7zz`); o Sambinha se recusa a rodar com versões anteriores. Ubuntu 22.04 e Linux Mint 21 trazem rclone 1.53 e p7zip 16.02, que são antigos demais: instale os binários oficiais de <https://rclone.org/downloads/> e <https://www.7-zip.org/download.html> (`7zz` para Linux x64) em `/usr/local/bin`, ou, no Ubuntu 24.04/Debian 13, use `sudo apt install rclone 7zip`. Depois configure `compressor = "7zz"` nos jobs em lotes e confira com `rclone version` e `7zz`.

```bash
sudo apt install git python3 python3-venv
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
```

No macOS, instale as ferramentas com o Homebrew em vez do apt (`brew install git python@3.12 rclone sevenzip`; o compactador também se chama `7zz`) e depois execute os mesmos passos de `git clone`, `venv` e `pip install`. Leia as [observações sobre macOS](docs/configuration.pt-BR.md#macos) antes de agendar os backups.

O repositório é privado. Pacotes de instalação também estão disponíveis nas [releases](https://github.com/lesantos/sambinha/releases).

## Atualizar

Seus arquivos TOML, o estado e os logs ficam fora do clone e da `.venv`, então atualizar só troca o código. No diretório do clone:

```bash
cd sambinha
git pull --ff-only origin main
.venv/bin/python -m pip install --upgrade .
.venv/bin/sambinha --version
```

Para instalar uma release específica em vez da `main`, baixe as tags e faça checkout de uma (`git fetch --tags` e depois `git checkout v0.18.3`); para voltar à `main`, use `git checkout main`. Leia o [CHANGELOG.pt-BR.md](CHANGELOG.pt-BR.md) antes de reativar os agendamentos: algumas releases removem ou renomeiam opções, e o Sambinha recusa uma configuração que ainda as use. Uma boa conferência depois de atualizar é `.venv/bin/sambinha --config sambinha.toml --dry-run`.

Observações:
- `pip install --upgrade .` só reinstala quando a versão em `pyproject.toml` mudou. Se você estiver testando um commit não publicado com a mesma versão, acrescente `--force-reinstall --no-deps`.
- `git pull --ff-only` para em vez de fazer merge quando o clone tem commits locais ou divergiu; faça commit ou `git stash` das edições locais antes.
- Se a `.venv` foi criada com um Python que foi atualizado ou removido (por exemplo, depois de atualizar o sistema operacional) ou se o `pip` falhar com erros de importação, recrie-a: `rm -rf .venv && python3 -m venv .venv && .venv/bin/python -m pip install .`. Nada mais se perde, porque a `.venv` não guarda dados.
- Se `rclone` ou `7zz` também mudaram, confira as versões de novo com `rclone version` e `7zz` (os mínimos estão em [Instalar](#instalar)).
- Um agendador (cron, timer do systemd ou launchd) que chama `.venv/bin/sambinha` usa a versão nova na próxima execução; não é preciso reiniciar nada. Uma execução em andamento continua com o código antigo até terminar.

## Configurar e executar

Crie `sambinha.toml`. Os caminhos são relativos a esse arquivo; `~` e variáveis de ambiente são aceitos. Mantenha destino, estado e logs fora da origem e preserve o diretório de estado.

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"

[[jobs]]
name = "documents"
source = "~/Documentos"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
log_retention_days = 14
verification_mode = "simple"
verify_source_changes = true # Retenta alterações na origem até 3 vezes; depois falha.
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
.venv/bin/sambinha verify-local-metadata --config sambinha.toml --job documents
.venv/bin/sambinha restore --config sambinha.toml --job documents --output /mnt/recovered
```

A simulação executa `rclone sync --dry-run` e grava um log sem alterar os dados do backup. Jobs simples não criam mais o índice de arquivos do Sambinha; `max_delete` é um limite absoluto por job passado ao rclone. O histórico de `--backup-dir` expira pelo maior valor entre `deleted_days` e `version_days`; como o rclone não identifica se o item foi apagado ou substituído, ambos usam essa retenção conservadora. A restauração aceita `--path` para arquivos e diretórios. O [exemplo completo](sambinha.example.pt-BR.toml) inclui todas as opções.

## Comportamento essencial

- **Armazenamento e verificação:** `logic = "simple"` delega a sincronização da árvore e as exclusões ao rclone; `"batch"` cria arquivos compactados com índice. `sambinha verify-local-metadata` usa `rclone check` nos jobs simples; em batch, `verify-local-metadata` compara tamanho/data da origem com JSONs locais. `verify-destination-metadata` e `verify-destination-integrity` conferem separadamente as cópias e a integridade. Veja [checagens dos lotes](docs/batch-checks.pt-BR.md).
- **Links simbólicos:** links na origem são ignorados e nunca recriados no destino. Links conflitantes no destino são substituídos por arquivos/diretórios reais sem alterar seus alvos; links sem conflito ficam intactos. [Decisão e limites](docs/usage.pt-BR.md#links-simbólicos).
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


## Lotes e recuperação na versão 0.13.0

Consulte [lotes persistentes, configuração unificada, alertas finais Cronitor, rebalanceamento manual e recuperação por gerações](docs/batch-lifecycle.pt-BR.md) para os comportamentos atuais e a migração da versão 0.12.0.
