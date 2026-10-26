# Plano de execução — Sambinha 0.16.0 e 0.17.0

Documento de handoff. Tudo aqui já foi revisado e aprovado por Leandro; o executor deve
implementar o que está definido, sem reabrir decisões de design. Perguntas só se algo
aqui for contraditório com o código real.

## Estado em 2026-10-26

- `main` = 0.15.1 publicada (tag `v0.15.1`, CI verde: lint, Python 3.12, 3.13, macOS).
- Worktrees em `/home/leandro/dev/sambinha-wt/`:
  - `identity` (branch `identity`): Fase 3 EM ANDAMENTO por um agente; pode haver commits e
    alterações não commitadas. Primeiro passo do executor: `git -C ../sambinha-wt/identity status`
    e `git log main..identity`; continuar dali, não recomeçar.
  - `resume` (branch `resume`): vazio (igual a `main`). Fase 2.2–2.4 ainda não começou.
- Se os worktrees não existirem mais: `git worktree add -b <branch> ../sambinha-wt/<branch> main`.

## Convenções obrigatórias

- Leia `docs/architecture.md` e `docs/batch-lifecycle.md` antes de tocar em índice/lotes; estão corretos.
- Código: estilo denso existente; comentários, docstrings, nomes de teste e assuntos de commit em
  inglês; mensagens ao usuário em português. Toda mudança com teste de regressão.
- Invariantes de segurança: nunca seguir symlinks (walk com dir_fd/O_NOFOLLOW; `reject_symlinks`;
  `private_directory`), detecção de mudança na origem, diretórios de metadados 0700, trava de
  escritor único (flock), guard de remoção em massa.
- Docs sempre nos dois idiomas (`X.md` e `X.pt-BR.md`). CHANGELOG.md e CHANGELOG.pt-BR.md: entrada
  nova no topo, `## X.Y.Z — AAAA-MM-DD`, um parágrafo. Versão em 4 lugares: `pyproject.toml`,
  `sambinha/__init__.py`, `README.md`, `README.pt-BR.md`.
- Comandos (sempre a partir da raiz do worktree, python do venv compartilhado
  `/home/leandro/dev/sambinha/.venv/bin/python`):
  - módulo: `.venv/bin/python -m pytest tests/test_X.py -q`
  - suíte: `SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m pytest -n auto -q` (~5 min; deve passar)
  - `.venv/bin/ruff check .` e `.venv/bin/mypy sambinha` devem ficar limpos
  - `make lint typecheck test coverage` existem
- Release: bump das 4 versões, CHANGELOGs, commit `Release Sambinha X.Y.Z: <resumo>`, push, esperar
  CI verde (incluindo job `macos`), `git tag -a vX.Y.Z -m "Sambinha X.Y.Z"`, push da tag,
  `.venv/bin/python -m build --outdir dist`, `sha256sum` dos dois artefatos em `dist/SHA256SUMS`,
  `gh release create vX.Y.Z dist/sambinha-X.Y.Z* dist/SHA256SUMS --title "Sambinha X.Y.Z" --notes-file <parágrafo pt-BR>`.
- Trabalho paralelo por agentes: um worktree por branch, partição por arquivos, sem editar
  CHANGELOG/versão; merge em `main`, suíte completa, depois CHANGELOG. Evitar mais de 1–2 agentes
  simultâneos (limite de crédito por requisições em voo).

## Decisões já tomadas (não reabrir)

- Identidade entre máquinas = NOME do job (schema 2). Sem campo `job_id`.
- Órfãos no destino: primeira versão só RELATA; remoção apenas com `--prune-orphans`, nunca em
  `.sambinha/history`, respeitando o guard de remoção em massa.
- `--fresh-start` / TOML `resume = "auto" | "fresh"`: descarta caches e journals, mantém
  `state.active/retired/excluded` (detecção de arquivos alterados continua pela comparação com os controls).
- CLI migra para subcomandos (`sambinha restore --job x`); quebra de ordem de flags aceita e
  registrada no CHANGELOG; `sambinha` sem subcomando continua sendo `backup`.
- pytest só como executor (testes continuam unittest).

---

## 0.16.0 — Fase 3 (identidade) + 2.2/2.3/2.4 (retomada)

### 3. Identidade por nome (branch `identity`)

Hoje `state.identity(job)` = `{source, destination, type, logic, namespace}` e é comparada em
`state.load_state` (ForeignStateError), `index_generations` (`remote_index`/`_archive`,
`backup_state` vs `initialization.json`, `publish` grava identidade), `metadata.py`
(`cached_control`, manifest `.index.json`), `recovery_index.read_index`. Segunda máquina com
origem diferente e destino não vazio falha.

Especificação:
1. Identidade schema 2 = `{"schema": 2, "name": job.name, "type", "logic", "namespace": NAMESPACE}`
   mais `"destination": job.destination` SOMENTE quando `job.type == "s3"`.
2. Caminhos viram informativos: `state["origins"]` = lista de `{host, platform, source, destination,
   last_seen}`; upsert da máquina atual a cada save; máximo 8 entradas.
3. Uma função `state.identity_matches(stored, expected, *, adopt=False)`:
   - schema 2 vs schema 2: dicts iguais;
   - stored legado (sem `name`): aceito quando source/destination/type/logic legados são iguais aos
     do job atual (migração automática na máquina original: reescrever como schema 2 no próximo
     save e marcar `index_pending` para republicar o índice remoto); caso contrário
     ForeignStateError, salvo `adopt=True`;
   - `adopt=True` também aceita schema 2 que difere só em `name` (job renomeado) e reescreve.
4. Flag `--adopt-identity` (só backup; encadear como `allow_mass_deletion` em cli/engine/config);
   evento `identity_adopted` com identidade antiga e nova.
5. Marcador no destino `.sambinha/job.json` = `{"schema": 2, "name", "type", "logic", "created"}`,
   gravado no publish quando ausente/diferente (nunca em dry_run). Em `backup_state`, marcador com
   nome diferente e sem adopt → ForeignStateError
   `"Destino pertence ao job X; use --adopt-identity para assumir"`.
6. Arquivo de estado: gravar `schema: 2` quando a identidade for schema 2; continuar lendo 1.
   Versões antigas devem recusar schema desconhecido com UnsupportedStateError (verificar).
7. Cache de fingerprints inclui st_dev/st_ino → em outra máquina faz miss total; garantir rehash
   completo sem erro e documentar em `docs/performance*.md`.
8. Testes: ajustar os que fixam o comportamento legado (`test_index_generations` origem `/other`
   recusada, `test_metadata`, `test_local_integration` namespace, `test_services`, `test_hardening`,
   `test_recovery`). Novos: schema 2 ignora source/destino local e mantém destino S3; índice remoto
   adotado em segunda máquina (mesmo nome, origem diferente, destino não vazio, sem estado local)
   para simple e batch, com restore lá; nome diferente recusado com a mensagem do marcador; estado
   legado na mesma máquina migra e republica; estado legado com caminhos diferentes exige
   `--adopt-identity` e então funciona; job renomeado exige `--adopt-identity`; dois hosts
   alternando backups no mesmo destino e restore num terceiro; `origins` com upsert e limite;
   manifest em metadata_dir compartilhado aceito; mudança de st_dev rehasheia sem erro.
9. Docs: `batch-lifecycle` (seção identidade), `configuration` (subseção "Mesmo backup em várias
   máquinas": nome é a identidade; renomear exige `--adopt-identity`; pontos de montagem por máquina
   via `source_mount`/`destination_mount` ou `${VARS}` no TOML), `architecture`.

### 2.2a Journal de uploads (branch `resume`)

Ler antes: `strategies/batch.py` (`_plan_batches`, `_pending_uploads`, `_upload_pending`,
`upload_batch`, `batch()`), `batches.py` (`plan()` com `previous`), `context.py`,
`index_generations.py` (`publish`/`finish`/`backup_state`), `storage.py` (`put` usa
`rclone copyto --checksum`), `tests/test_batch_lifecycle.py`, `tests/test_batch_staging.py`.

- Após cada `upload_batch` bem-sucedido, gravar (atomicamente via `state.atomic_json`, sob
  `threading.Lock` no Context, pois os workers são threads) uma entrada em
  `state/<job>/uploads.json`: `{key, batch_id, archive_size, archive_md5,
  files:[{path,size,mtime_ns,sha256,md5,mode}], uploaded_at}`.
- Em `_plan_batches`, alimentar as entradas do journal como `previous` adicional em `batches.plan()`
  (mesmos arquivos formam o mesmo lote).
- Em `_pending_uploads`, tratar chave do journal como já enviada quando o objeto existe no destino
  com o tamanho do journal (reusar a listagem `_available`) e os arquivos do journal batem
  exatamente com o inventário atual (path/size/mtime_ns e hashes em modo hash); caso contrário
  descartar a entrada.
- Entradas consumidas/limpas no commit bem-sucedido (onde `ctx.save()` ocorre após `upload_index`);
  chaves do journal que não entraram no novo `active` viram órfãos (2.2b). Dry-run nunca grava journal.

### 2.2b Relato de órfãos

- Após commit bem-sucedido, usando a listagem do destino já obtida na execução quando existir
  (não acrescentar segunda listagem completa), calcular objetos sob o destino do job não
  referenciados por active/excluded/retired/journal (archives e controls `.json`), fora de
  `.sambinha/history`, e `.sambinha/indexes/<geração>.tar.gz` sem `.commit.json` par.
- Um evento `orphans` em nível summary com `count`, `bytes`, idade do mais antigo e até 20 chaves
  de exemplo; nada quando zero.
- Flag `--prune-orphans` (só backup): apaga esses objetos somente com idade > 1 dia, respeitando o
  guard de remoção em massa existente (ver como `max_delete`/guard é aplicado e espelhar),
  evento `orphan_removed` por chave; nunca toca `.sambinha/history` nem o journal corrente.

### 2.3 Reinício limpo

- Flag `--fresh-start` e opção de job no TOML `resume = "auto"` (padrão) | `"fresh"`
  (validar em `config_validation`).
- Fresh descarta: cache `fingerprints` e `last_full_scan` (força scan/rehash completo),
  `uploads.json`, journal `publication.json`, e dispara a varredura de temporários (2.4).
  MANTÉM `state.active/retired/excluded`.
- Evento `fresh_start` listando o que foi descartado.

### 2.4 Varredura de temporários

- Prefixos de `tempfile.TemporaryDirectory` passam a incluir o pid: `sambinha-<etapa>-<pid>-`
  (grep: discovery, batch, index, bootstrap, control, rebalance, verification, restore).
- No início da execução (após obter a trava de escritor, em `engine.run` ou no preflight do batch)
  remover `sambinha-*-<pid>-*` no temp dir efetivo cujo pid não está vivo (`os.kill(pid, 0)` →
  ProcessLookupError; PermissionError = vivo) e com mais de 1 hora; evento `temp_cleanup` com
  contagem/bytes; nunca seguir symlinks (rmtree só em diretórios reais).

### Testes de 2.2–2.4

- Retomada: backup batch com 3 lotes onde o 2º upload falha após o 1º ter sucesso (patch em
  `storage.put`); rodar de novo e afirmar que o 1º lote não é recomprimido nem reenviado (contar
  invocações do compressor / `put`) e o estado final está correto.
- Entrada do journal descartada quando um membro mudou.
- Relato de órfãos com archive não referenciado e tar.gz de geração sem marcador
  (count/bytes/exemplos); zero órfãos → sem evento.
- `--prune-orphans` apaga só objetos antigos e respeita o guard.
- `--fresh-start` força rehash (fingerprints somem), mantém active, ainda detecta arquivo alterado,
  limpa journals; `resume = "fresh"` no TOML equivalente.
- Varredura: remove dir de pid morto com >1h, mantém dir de pid vivo e dir jovem, ignora symlinks.
- Dry-run não grava journal.

### Docs de 2.2–2.4

`batch-lifecycle` (seção interrupção e retomada, relato de órfãos, reinício limpo),
`configuration` (`resume`, `--fresh-start`, `--prune-orphans`), `usage`, `architecture`.

### Fechamento da 0.16.0

Merge de `identity` e `resume` em `main` (conflitos esperados em `cli.py`, `index_generations.py`,
docs), suíte completa, ruff, mypy, CHANGELOG nos dois idiomas, release conforme convenções.

---

## 0.17.0 — Fase 4 (performance) + 5.6–5.10 (arquitetura)

Cada item de performance com medição antes/depois (estender `scripts/benchmark-inventory.py` ou
criar `scripts/benchmark-backup.py`) registrada em `docs/benchmarks/` e em `docs/performance*.md`.

### 4. Performance (ordem de impacto)

4.1 `verify()` final (`strategies/batch.py` → `inventory.verify`) relê toda a origem em modo hash a
cada execução com qualquer mudança. Em hash mode com `fingerprint_cache` ativo e sem full scan
devido: comparar assinatura stat com a capturada no scan e só re-hashear quando divergir; percorrer
por diretório com dir_fd/O_NOFOLLOW reaproveitando a estrutura do `scan` (acelera também o modo
simple, hoje ~7+profundidade syscalls por arquivo). Manter releitura total quando
`fingerprint_cache=false` ou no dia de full scan. Testes: `test_verification_modes`,
`test_performance`, `test_batch_lifecycle`.

4.2 Descoberta (`discovery.py`): uma requisição `operations/list` por diretório (`recurse: False`,
~30 ms cada). Mudar para `recurse: True` por subárvore de primeiro nível, indexado por pai em
memória; walk local idêntico (stat relativo a dir_fd, validação IsDir vs tipo local, ordem por
mtime). Deadline por subárvore. Testes: `test_discovery` (contagem de chamadas), `test_filtering`,
benchmark.

4.3 `include_paths` (`filtering.py`; chamadas em `context.py` e `strategies/batch.py`) reclassifica
todos os arquivos indexados a cada execução (N arquivos vazios + `rclone lsjson`). Classificar só
`before_files − current_files` e retirados: os presentes no inventário atual foram selecionados
pelas mesmas regras congeladas. Testes: `test_filtering`, `test_performance`, `test_batch_lifecycle`
(excluded).

4.4 `state.json` carrega `fingerprints` mesmo em modo simple (nunca consultados) e os embute no
índice de recuperação (`MAX_METADATA` 64 MiB ≈ 200–250k arquivos). Não persistir em simple; em
hash, arquivo separado `fingerprints.json` fora do índice publicado; eliminar os dois `deepcopy`
do estado (`strategies/batch.py`). Atenção a `inventory_changed` (`jobs.py`). Testes:
`test_performance`, `test_index_generations`, `test_recovery`, `test_hardening`.

4.5 `finish()` (`index_generations.py`) lista o destino inteiro só para podar `.sambinha/indexes`:
listar só o prefixo. Testes: `test_index_generations`.

4.6 Hash: blocos de 1 MiB; md5 em thread paralela (hashlib libera o GIL); para o arquivo gerado
calcular só md5 (sha256 não é usado); comparar CRC32 do `7z l -slt` com CRC32 calculado no
inventário em hash mode (fecha a lacuna de integridade "7-Zip leu o que o inventário viu").
Testes: `test_inventory`, `test_batch_staging`.

4.7 Controls locais lidos/validados duas vezes por execução (`context.py` `prepare_controls` e
`strategies/batch.py`): memoizar por execução. Testes: `test_metadata`.

4.8 rclone: `--fast-list` opcional (TOML) para jobs simple em S3; expor `s3_chunk_size` /
`s3_upload_concurrency` para uploads multi-GB. Documentar que `--checksum` em hash mode força md5
de toda a origem a cada sync.

### 5.6–5.10 Arquitetura

5.6 Fachada pública `sambinha/api.py` com `__all__` (run, recover, load, validate, Config, Job,
BackupError, STRATEGIES); remover re-exports `as X` de `engine.py`/`recovery.py`; testes importam
da origem real (`retention.DAY`, `context.Context`, `state.identity`, `archive_recovery.unpack`,
`verification.*`). Registrar no CHANGELOG.

5.7 Dataclasses: `Job`/`Config` com `slots=True, kw_only=True` (manter `replace()` compatível e
name/source/destination posicionais via `KW_ONLY`); mover os 15 campos "override" de `Config` para
função `with_job_overrides(config, **values)` ou dataclass `JobOverrides`; `Context` separado em
parte imutável (config, job, storage, directory, identity) e mutável (metrics, removed, max_delete);
substituir `copy.copy(ctx)` nos workers por método `worker_view(runner, progress)`.

5.8 Storage: `StorageBackend(Protocol)` tipado em `storage.py`; `SimpleStorageMixin` vira classe
concreta por composição (recebe o adapter); `STRATEGIES` inclui `"simple"` e `JobExecution`
despacha uniformemente; `Context` aceita `storage_factory` opcional.

5.9 CLI: `argparse.add_subparsers(dest="action")` com parent parser para overrides; parsers
`restore`/`rebalance`/`verify-*` só com suas flags; `sambinha` sem subcomando = backup; help gerado
de uma tabela única nome→(tipo, help) que também alimenta `config.load` (elimina as listas de nomes
em `cli.py`). Testes unitários de parser. CHANGELOG: ordem nova `sambinha restore --job x`.

5.10 Testes: mover `use_batch`/builder de job para `tests/helpers.Sandbox`; separar `tests/unit`
(sem rclone) de `tests/integration`; `FakeRunner` que grava comandos para cobrir `cli.py` 60–68,
`storage.py` 79–88, `recovery_index.py` (62%), validações de `state.py`; subir `fail_under` para 92.

### Fechamento da 0.17.0

Suíte, ruff, mypy, benchmarks registrados, CHANGELOG nos dois idiomas (incluindo a quebra da CLI
e da fachada), release.

---

## Itens de menor prioridade (fazer se sobrar tempo, sem release própria)

- `compress()` não prova o que o 7-Zip leu: resolvido parcialmente por 4.6 (CRC32).
- Sobreposição origem/destino em sistemas case-insensitive (macOS APFS): comparar `(st_dev, st_ino)`
  dos ancestrais existentes em `paths.overlap`; no restore, detectar colisões `casefold()`/NFC entre
  membros antes de extrair e falhar com mensagem clara.
- `storage.py` lê `getattr(self.runner, "last_stderr", None)`: expor `runner.last_report()` tipado.
- Funções >50 linhas: `inventory.scan`, `config.load` (extrair `normalize_global()`/`normalize_job()`),
  `config_locations.key_lines`, `progress.task`, `state.load_state` (extrair `validate_retired`/
  `validate_fingerprints`), `process._run_with_progress`, `config_validation._validate_job`.
