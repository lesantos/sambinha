# Sambinha

Esse arquivo define as características do Sambinha, um programa de backup cujo executável é `sambinha`.

## Requisitos não funcionais

- deve ser escrito em python (versão mais recente disponível)
- não deve necessitar de permissão de root para rodar
- o código deve ser legível e organizado de acordo com padrões de arquitetura modernos
  - utilize módulos com domínios bem separados
  - comente o código indicando o comporatamento esperado daquela região
  - deve ser o tipo de código que um ev experiente teria orgulho de compartilhar no github.
- o projeto deve ser salvo no github (conta lesantos)
- crie um README explicativo com principais funcionalidades, regras e forma de usar
- utilize aplicativos e programas disponíveis no linux quando isso simplificar o código
  - rclone ou restic são exemplos que eu espero que sejam utilizados
- antes de iniciar o backup, faça uma verificação para garantir que todas as dependências estão instaladas
- testes
  - crie testes unitários para as principais partes do sistema
  - crie uma estrutura de diretórios para os testes e versione junto
  - crie testes para todas os tipos e lógicas de backup
  - crie mocks para dependências ou serviços externos (utilize bibliotecas ou aplicativos previsamente existentes, se possível)

## Requisitos funcionais

- ter um arquivo de configuração com os parâmetros necessários para a execução.
  - Utilize um formato padrão de mercado para esse arquivo
- aceitar sobreposição desses comandos via linha de comenado (parâmetros não usados serão lidos do arquivo de configuração)
- aceitar indicar arquivos de configuração alternativos via parâmetro da linha de comando
- Aceitar diferentes tipos de backup. Inicialmente, serão implementados os dois abaixo, mas a estrutura do código deve suportar novos tipos facilmente no futuro
- em caso de erro no envio por erros desconhecidos ou erros de rede, retentar até 3 vezes o envio com (esperar 2 segundos entre retentativas)
- Pode configurar quais diretórios serão considerados para backup
- Cada diretório pode ser configurado para diferentes tipos e lógicas de backup ao mesmo tempo
- Respeitar as lógicas de backup (definidas abaixo)
- Gerar um log a cada execução
  - horário de começo e fim da execução
  - diretórios considerados
  - arquivos que foram de fato transferidos
  - quais arquivos não foram transferidos por já estarem idênticos no destino
  - eventuais erros
- Notificar execuções (sucessos ou erros) através desse link do cronitor (deixar configurável): <${CRONITOR_URL}>
- Configurar quantos dias manter no destino arquivos que foram apagados na origem (padrão 10 dias)
- Configurar quantos dias manter versões anteriores dos arquivos editados (padrão 2 dias)
- Ter um parâmetro para rodar o backup em modo simulação. Mostrar o que seria feito, mas não executar criação de lotes ou transferências.

### Tipos de backup

- cópia para um diretório local (ou montado na máquina onde roda)
- amazon S3
  - utilizar cold storage como padrão
  - opção para utilizar outros tipos de storage se assim configurado
  - incluir comentário no arquivo de configuração e código sobre os diferentes tipos de storage do S3, suas características e custo

### Lógicas de backup

#### regras para todas as lógicas

- copie apenas caso os arquivos não existam idênticos no destino
- utlize programas do linux para fazer a cópia e verificar necessidade de cópia

#### simples

- essa lógica assume que existirão dados duplicados na origem e destino
- faça uma cópia exata de todos os arquivos
- mantenha atributos e permissões dos arquivos sempre que possível

#### otimizada para S3

- essa lógica é feita para otimizar o custo de armazenamento e recuperação dos dados no S3
- arquivos serão amazenados em lotes
- cada lote será compactado e enviado como um único arquivo
- após a transferênica para o destino, os lotes criados serão apagados, sendo mantidos apenas no destino.
- programa usado para compactação é configurável
  - padrão é 7z
- arquivos de um mesmo diretório sempre devem estar no mesmo lote
- um diretório pode ter mais de um lote
- as caracterísiticas de cada lote devem ser configuráveis
  - tamanho máximo (padrão 1Gb)
  - quantidade máxima de arquivos (padrão 1000 arquivos)
- lotes devem ter pelo menos dois arquivos. Caso um arquivo ultrapasse o tamanho máximo configurado para os lotes, ele deve ser armazenado individualmente (utilizar a mesma compactação) - nesse caso o nome do lote deve ser o mesmo nome do arqiuivo, sendo alterada apenas a extenção.
- lotes devem ser amazenados na mesma estrutura de diretórios de seus arquivos originais (caminho da raiz até o arquivo mais próximo da raiz. Lotes podem ter arquivos e árvores de diretórios dentro dele.). A intenção é poder navegar pela estrutura dos lotes para encontrar facilmente um lote específico.
- Ao lado de cada lote (mesmo diretório) deve ser criado um arquivo texto indicando o conteúdo daquele lote. Esse é o arquivo de controle de lote.
  - esse arquivo será utilizado para verificar o conteúdo do lote sem precisar fazer o download ou descompactar o lote.
  - aramazenar nesse arquivos todas os metadados necessários para que ele seja utilziado para verificar se arquivos dentro daquele lote foram alterados.
- Apenas os lotes e seus arquivos de controle de serão enviados ao destino. Arquivos originais não serão enviados.
- Manter uma estrutura local com a estrutura de diretórios e arquivos de lotes (local configurável). Assim podemos verificar os lotes localmente, sem precisar consultá-los no destino.
- Enviar um arquivo de backup compactados contendo uma cópia dessa estrutura de controel de lotes para o destino. Se o destino for do tipo S3, esse arquivo não deve ser guardado em cold storage, mas sim em storage comum.


## Clarificações da implementação

- Python disponível nos pacotes do Linux Mint (3.12), conforme orientação do usuário.
- Lotes acumulam arquivos de vários diretórios até atingir os limites, preservando a árvore interna.
- Testes verificam completude e integridade por restauração em ambas as lógicas e destinos.
- Credenciais AWS e Cronitor ficam fora do repositório.
