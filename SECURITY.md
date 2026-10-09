# Cuidados com credenciais

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
