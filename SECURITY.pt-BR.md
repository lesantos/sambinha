# Cuidados com credenciais

[English](SECURITY.md)


Credenciais devem ficar fora da pasta do projeto. Use `secrets_file` para carregar um arquivo privado e mantenha exemplos somente com placeholders. As notificações usam `CRONITOR_URL`; uma URL completa de telemetria contém credenciais e não deve ser compartilhada ou versionada.

## Auditoria desta revisão

Foram conferidos os arquivos atuais, todas as versões alcançáveis no Git, todos os 141 blobs locais (inclusive objetos não alcançáveis) e o conteúdo extraído do wheel publicado em `v0.2.0`. A busca pelos valores privados conhecidos encontrou **zero ocorrências no histórico**. O Gitleaks 8.30.1, com checksum do pacote oficial conferido, também encontrou **zero credenciais** no histórico, no snapshot atual e no artefato publicado.

A URL privada estava em arquivos locais ignorados pelo Git; esses arquivos foram saneados, e o segredo foi retirado da pasta do projeto. Não foi necessário reescrever commits antigos, porque não havia ocorrência privada no histórico auditado. Nenhum valor de credencial aparece neste relatório.

## Verificação contínua

A CI busca o histórico completo (`fetch-depth: 0`) e executa o Gitleaks com todas as referências Git, saída redigida e checksum de download validado. A configuração `.gitleaks.toml` mantém as regras padrão e acrescenta uma regra para URLs de telemetria Cronitor com credenciais. Os testes verificam que a regra detecta esse formato sem armazenar tokens reais no código.

Com o Gitleaks instalado, execute:

```bash
./scripts/check-secrets.sh
```

Uma verificação automática depende dos formatos de credenciais conhecidos; exemplos nunca devem conter dados reais. Se uma credencial real for publicada em algum momento, revogue-a no serviço antes de corrigir o histórico: apagar o texto não invalida cópias já obtidas.

## Proteções de código em 0.4.1

O índice e controles são validados antes do uso: metadados, checksums, caminhos relativos, membros duplicados e conflitos entre dados ativos e histórico. Caminho raiz, travessia, caracteres nulos e quebras de linha são recusados. Diretórios de metadados/log usam 0700; novos arquivos JSON/log/lock usam 0600, independentemente de umask permissivo. Links em diretórios de metadados e redirecionamento do lock são recusados.

Valores conhecidos de credenciais, URLs explícitas do Cronitor, senhas do rclone e usuário/senha em URLs são ocultados em erros, logs e telemetria. A ocultação ocorre antes de escapar o JSON, cobrindo segredos multilinha. Restauração exige pasta nova/vazia separada, valida membros antes da extração, rejeita links/caminhos inseguros, confere conteúdo temporário e cria arquivos exclusivamente, sem bits privilegiados. Tars de recuperação não são extraídos indiscriminadamente.

O bloqueio de remoções e checks de 95% ocorrem antes de mudanças no destino. Subprocessos não usam interpolação de shell; listas literais evitam interpretar curingas/opções. Testes exercitam as falhas. Permissões/validação não autenticam índices contra um atacante com a mesma conta/root. Verificação S3 não inspeciona a extração dos lotes. Desde 0.6.1, `verification_mode = "simple"` compara tamanho/data por padrão e não detecta edições que preservem ambos. Use `"hash"` para comparar conteúdo: MD5 atende o contrato S3/rclone e conteúdo restaurado com hashes registrados também é conferido por SHA-256. Lotes e controles mantêm checksums nos dois modos.

A auditoria histórica relatada acima é anterior a esta release; os metadados Git protegidos deste workspace não constituem uma nova auditoria integral do histórico.

Para 0.6.0, o snapshot preparado passou no Gitleaks 8.30.1 sem ocorrências. O executável foi conferido pelo checksum oficial; o histórico alcançável é verificado novamente antes da publicação.

Referências: [OWASP para logs](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [OWASP sobre argumentos de comandos](https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html), [Python sobre extração de arquivos](https://docs.python.org/3.12/library/tarfile.html#extraction-filters).
