# Eleição em Anel e Two-Phase Commit (MC714, Trabalho 2)

Implementação dos dois algoritmos distribuídos do Trabalho 2 como **Nós
independentes que conversam por HTTP/JSON sobre Docker Compose**: a **Eleição de
Líder em Anel**, com cinco Nós formando o anel 1 → 2 → 3 → 4 → 5 → 1, e o
**Two-Phase Commit (2PC)**, com um Coordenador e três Participantes, cada um com
seu Log_Duravel. Os dois algoritmos sofrem injeção de falhas tanto no nível da
aplicação (crash, pause, delay, drop) quanto de rede (latência, perda e partição
via Toxiproxy), e cada execução registra a Semente e o ambiente para ser
reproduzível.

O repositório entrega os dois algoritmos, a orquestração de experimentos que os
exercita em processo e sobre o compose, a camada de análise que consome os CSVs
coletados, os invariantes de correção sobre os logs dos Nós reais e as respostas
de modelo e correção em [`docs/`](docs/).

## Índice

- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Uso das CLIs](#uso-das-clis)
- [Docker Compose](#docker-compose)
- [Arquitetura](#arquitetura)
- [Arquivos de saída](#arquivos-de-saída)
- [Reprodutibilidade](#reprodutibilidade)
- [Documentos de correção](#documentos-de-correção)
- [Verificação](#verificação)
- [Limitação do ambiente](#limitação-do-ambiente)
- [Divisão de trabalho da dupla](#divisão-de-trabalho-da-dupla)
- [Uso de IA generativa](#uso-de-ia-generativa)

## Requisitos

- **Python 3.11 ou superior** para as CLIs de orquestração, análise e verificação.
- **Docker com Compose v2** para subir os Nós em contêiner. A análise, os
  invariantes e a bateria do Anel rodam **sem Docker**. A bateria do 2PC e os
  cenários sobre o compose (`scripts/cenario.py`, `scripts/demo.py`, os cenários
  de rede) o exigem.
- As dependências fixadas em `requirements.txt`:

```
fastapi==0.162.0    transporte HTTP/JSON dos Nós (servidor)
uvicorn==0.34.0     servidor ASGI que hospeda os apps dos Nós
httpx==0.28.1       cliente HTTP com Deadline obrigatório entre Nós
pandas==3.0.2       camada de análise
numpy==2.4.2        camada de análise
matplotlib==3.10.8  figuras
pytest==8.3.4       testes
```

`pydantic` entra como dependência transitiva do FastAPI, sem pin próprio.

## Instalação

A partir de `T2/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No Windows, troque a segunda linha por `.venv\Scripts\activate`.

A mesma `requirements.txt` é instalada dentro da imagem dos Nós (ver
[`Dockerfile`](Dockerfile)), de modo que o ambiente do contêiner e o ambiente das
CLIs fiquem alinhados.

## Uso das CLIs

As CLIs se dividem em três grupos: **experimentar** (gerar dados), **analisar** e
**verificar** — mais os **cenários/demos** que sobem o compose. Todas resolvem
`src` a partir da raiz do projeto, então rodam de qualquer diretório.

### 1. Bateria de experimentos (`scripts/bateria.py`)

Executa Réplicas com Sementes independentes variando **um** parâmetro em pelo
menos três níveis e grava CSVs em `results/`. A do Anel roda **em processo**,
dirigindo a Máquina_de_Estados pura, e é determinística por Semente. A do 2PC
mede os Nós reais: cada Réplica sobe o compose com o Toxiproxy entre o
Coordenador e os Participantes, aplica a latência do nível e derruba tudo no fim.

```bash
python scripts/bateria.py --algoritmo anel                         # varia num_nos (3, 5, 7)
python scripts/bateria.py --algoritmo anel --variar taxa_falhas    # varia (0.0, 0.1, 0.2)
python scripts/bateria.py --algoritmo 2pc                          # varia latencia_rede (0, 10, 25, 50 ms), cerca de 10 min
```

Para o Anel, as métricas são Tempo_de_Convergencia (em passos lógicos de entrega
de Mensagem) e número de Mensagens. Para o 2PC, são a latência de commit vista
pelo cliente, as mensagens por transação e o tempo de recuperação depois de um
crash do Coordenador. A bateria valida a configuração inteira antes de
sobrescrever resultados, carimba a Semente e o ambiente em cada linha, e uma
Réplica que falha é registrada sem interromper o restante.

| Opção | Padrão | Descrição |
|:---|:---|:---|
| `--algoritmo` | — (obrigatória) | `anel` ou `2pc` |
| `--variar` | `num_nos` / `latencia_rede` | parâmetro variado: `num_nos` ou `taxa_falhas` no anel, `latencia_rede` no 2pc |
| `--replicas` | 5 | Réplicas por nível |
| `--semente-base` | 42 | Semente base das Réplicas |
| `--transacoes` | 40 | 2pc: transações medidas por Réplica |
| `--aquecimento` | 5 | 2pc: transações de aquecimento por Réplica, gravadas e marcadas |
| `--results` | `results/` | diretório de saída |
| `--saida` | `results/bateria_anel_<variar>.csv` | anel: caminho do CSV |

A análise consome por padrão `results/bateria_anel_num_nos.csv`,
`results/bateria_2pc_latencia_rede.csv` e `results/bateria_2pc_recuperacao.csv`.

### 2. Análise (`scripts/analisar.py`)

Lê os CSVs da bateria e produz as tabelas markdown (prontas para colar em
`docs/`) e as figuras. **Não executa Réplica nem sobe contêiner.**

```bash
python scripts/analisar.py tudo
```

| Subcomando | O que produz |
|:-----------|:-------------|
| `anel` | convergência do Anel × número de Nós (tabela + figura) |
| `anel-complexidade` | mensagens do Anel medidas contra 2n − 1, com e sem perda |
| `2pc-complexidade` | mensagens e latência do 2PC medidas contra 4N e 2N atrasos de ida |
| `2pc-latencia` | latência de commit do 2PC × latência de rede, com p50/p95/p99 e mensagens por transação |
| `2pc-recuperacao` | tempo de recuperação após o crash do Coordenador × latência de rede |
| `conferir` | confere se as tabelas de `docs/` batem com os dados atuais |
| `tudo` | executa todos os anteriores, na ordem |

As mensagens de progresso vão para stderr e as tabelas para stdout, então a saída
pode ser redirecionada sem ruído. A conferência é literal: um dígito fora do
lugar reprova, o que garante que nenhuma tabela de `docs/` seja digitada à mão.

### 3. Verificação (`scripts/verificar.py`)

Roda os invariantes de correção (safety e liveness) sobre os
Logs_Estruturados e Logs_Duraveis que os Nós reais deixaram sob `results/`.

```bash
python scripts/verificar.py                       # verifica tudo sob results/
python scripts/verificar.py results/cenario_anel_falha_logs   # caminhos específicos
```

Sai com código **diferente de zero apenas quando um invariante reprova** — ou
seja, quando uma propriedade de *safety* é violada. Um aviso, como o
Estado_Em_Duvida bloqueante do 2PC enquanto o Coordenador está fora, aparece no relatório
mas não altera o código de saída, porque bloquear é comportamento correto do
algoritmo. A ausência de logs também não reprova. A lista de invariantes, com a
tolerância de cada um e a razão dela, está em
[`docs/invariantes.md`](docs/invariantes.md).

### 4. Cenários e demos sobre o compose

Enquanto a bateria mede tendência com muitas Réplicas, estes scripts sobem um
cenário **real** sobre o Docker Compose, conduzem o algoritmo pelos endpoints de
controle, coletam os logs dos Nós para `results/` e rodam os invariantes sobre
eles. Cada cenário confere o desfecho contra o esperado e sai 1 quando ele
difere ou quando um invariante reprova. Todos derrubam o compose ao final
(`down -v --remove-orphans`) e, sem Docker, saem com uma mensagem clara em vez
de travar.

```bash
python scripts/cenario.py anel normal        # eleição converge para o maior id (5)
python scripts/cenario.py anel falha         # mata o Líder (5) e reelege o maior vivo (4)
python scripts/cenario.py 2pc commit         # todos votam SIM -> COMMIT atômico
python scripts/cenario.py 2pc abort          # Participante 3 vota NAO -> ABORT atômico
python scripts/cenario.py 2pc crash-coord    # Coordenador cai após decidir -> dúvida -> reinício -> COMMIT

python scripts/demo.py                        # encadeia anel/falha + 2pc/crash-coord e verifica
```

As demos do núcleo e das falhas rodam **sem Docker**, com apps FastAPI locais em
portas efêmeras — úteis para ver o algoritmo sem subir o compose:

```bash
python -m scripts.demo_rede               # dois Nós trocando Mensagens; timeout no Deadline
python -m scripts.demo_anel               # eleição sobre o compose (supõe os Nós no ar)
python -m scripts.demo_falhas_anel        # crash do Líder e reeleição (apps locais)
python -m scripts.demo_falhas_2pc         # crash do Coordenador, bloqueio e recuperação
python -m scripts.demo_rede_toxiproxy     # latência e partição via Toxiproxy (compose)
```

### 5. Falhas de rede com Toxiproxy (`scripts/rede.py`)

Cria os proxies entre os Nós e aplica toxics de forma scriptada e repetível,
falando com o HTTP API do Toxiproxy (publicado em `127.0.0.1:8474`). Supõe o
compose já no ar com o override de roteamento `compose.toxiproxy.yaml`.

```bash
docker compose -f compose.yaml -f compose.toxiproxy.yaml up -d --wait
python -m scripts.rede anel semear                  # cria os 5 proxies do Anel
python -m scripts.rede anel latencia --ms 300       # latência acima do Deadline
python -m scripts.rede anel particionar --no 5      # partição do Líder -> reeleição
python -m scripts.rede anel curar                   # remove toxics e reabilita
python -m scripts.rede doispc semear
python -m scripts.rede doispc particionar --participante 1
```

## Docker Compose

Todos os serviços compartilham a **mesma imagem** (ver [`Dockerfile`](Dockerfile)):
o algoritmo de cada contêiner é escolhido pelo `command` do serviço, não por uma
imagem dedicada. A topologia é toda por variável de ambiente, nunca embutida no
código.

- **Seção Eleição em Anel:** `no-anel-1` .. `no-anel-5` na rede dedicada
  `rede-anel`, publicados em `8001..8005`. Cada Nó conhece o Anel e o mapa de
  URLs por ambiente e só difere no `NO_ID`.
- **Seção 2PC:** `coordenador` (publicado em `8000`) e `participante-1..3`
  (`8011..8013`) na rede `rede-2pc`. Cada Participante monta um **volume nomeado
  próprio** em `/dados` para o seu Log_Duravel, o que preserva a Decisao através
  de reinícios. Os Deadlines de Voto e de Decisao são configuráveis por ambiente.
- **Serviço Toxiproxy:** `ghcr.io/shopify/toxiproxy:2.9.0`, ligado às duas redes,
  com o HTTP API em `8474`. Por padrão apenas fica de pé; os Nós só passam a falar
  pelos proxies com o override `compose.toxiproxy.yaml`.

Comandos de ciclo de vida:

```bash
docker compose build                                   # constrói a imagem única
docker compose up -d --wait no-anel-1 no-anel-2 no-anel-3 no-anel-4 no-anel-5   # sobe o Anel
docker compose up -d --wait coordenador                # sobe o 2PC (puxa os Participantes)
docker compose down -v --remove-orphans                # derruba e limpa volumes e órfãos
```

Overrides pontuais:

- `compose.toxiproxy.yaml` — roteia o tráfego dos Nós pelo Toxiproxy.
- `compose.commit.yaml` — faz o Participante 3 votar SIM, para o cenário de COMMIT
  em todos (o base o deixa votando NAO para demonstrar o ABORT atômico).

Endpoints de controle de cada Nó:

| Algoritmo | Endpoints |
|:---|:---|
| Anel | `GET /estado`, `POST /iniciar-eleicao`, `GET /saude`, `POST /falha/*` |
| 2PC | `POST /transacao`, `GET /estado`, `GET /saude`, `POST /falha/*` |

Os endpoints `/falha/*` (crash, pause, delay, drop) são a porta pela qual a
injeção de falhas de aplicação é disparada, de forma determinística por Semente.

O `POST /transacao` aceita o id da transação no corpo, `{"transacao": "x"}`, e
gera um quando ele falta. Um id já usado devolve a Decisao que ele já tem, sem
abrir rodada nova.

## Arquitetura

Camadas com fronteira estrita. O núcleo comum em
`src/comum/` oferece config validada, modelos imutáveis, streams de
aleatoriedade por Semente, log estruturado JSON e o Transporte HTTP com Deadline
obrigatório. Cada algoritmo é construído da **Máquina_de_Estados pura** para fora:
a lógica testável em memória, depois o app FastAPI, depois o contêiner. A análise
em `src/analise/` lê os CSVs e logs coletados e produz tabelas, figuras e
invariantes.

**A regra de dependência:** a Máquina_de_Estados pura nunca importa FastAPI nem
httpx, e a camada de análise nunca sobe contêiner nem executa Réplica. O que
amarra a lógica pura à rede são `src/anel/no.py`, `src/doispc/coordenador.py` e
`src/doispc/participante.py`; o que orquestra experimentos é `src/experimentos/`.

```
src/comum/         config, modelos, aleatoriedade, log, rede (Transporte com
                   Deadline), despacho (fila de saída fora do handler),
                   falhas (crash/pause/delay/drop), toxiproxy
src/anel/          maquina (pura), no (app FastAPI), __main__ (entrada do contêiner)
src/doispc/        maquina (pura), log_duravel (write-ahead), coordenador,
                   participante, executor, __main__
src/experimentos/  bateria (planeja e executa Réplicas), runner_anel (em
                   processo), condutor_doispc (Nós reais no compose),
                   conteineres (compose e sondagem HTTP), ambiente (carimbo de
                   hardware/SO/versões)
src/analise/       dados, estatistica, formato, graficos, anel_exp, doispc_exp,
                   invariantes
scripts/           bateria, analisar, verificar, cenario, demo, rede e as demos
docs/              modelo e correção, invariantes, resultados, com dados/ e figuras/
results/           dados brutos (CSVs e JSON, no git), logs e Logs_Duraveis (fora dele)
```

O **Deadline é obrigatório por projeto**: o cliente do Transporte não expõe
caminho que envie Mensagem sem timeout. Sem isso, uma partição de rede travaria
um Nó indefinidamente, e o salto do Anel ou o ABORT por timeout do 2PC deixariam
de acontecer. No 2PC, a **ordem write-ahead** sustenta a durabilidade: Voto e
Decisao vão ao Log_Duravel antes de serem respondidos ou aplicados, e o
Coordenador grava a entrada em WAIT antes do PREPARE. Na partida, ele relê o log,
aborta o que estava em WAIT e difunde de novo o que estava decidido. A DECISAO é
retransmitida até o ACK de cada Participante.

## Arquivos de saída

Gravados em `results/`. Os CSVs das baterias e os registros JSON dos cenários são
os dados brutos da entrega e ficam no git. Os logs dos Nós e o stdout dos
contêineres são refeitos a cada execução e ficam fora dele. Há dois tipos.

### CSVs da bateria

Uma linha por Réplica no Anel e uma por transação na latência do 2PC. Além das
colunas de métrica, cada linha carrega colunas de ambiente com prefixo `amb_` (SO,
versão, arquitetura, processador, número de CPUs, versão de Python e das
dependências), o carimbo de reprodutibilidade descrito abaixo. As do 2PC trazem
também as versões do Docker e do Compose e o Python da imagem.

`bateria_anel_num_nos.csv` (e `bateria_anel_taxa_falhas.csv`):

| Coluna | Significado |
|:-------|:------------|
| `algoritmo`, `parametro_variado`, `semente` | identificam a Réplica |
| `num_nos`, `taxa_falhas` | parâmetros da Réplica |
| `tempo_convergencia` | passos lógicos de entrega até a convergência |
| `mensagens` | total de Mensagens entregues |
| `convergiu`, `lider`, `nos_vivos` | resultado da eleição |

`bateria_2pc_latencia_rede.csv`, uma linha por transação:

| Coluna | Significado |
|:-------|:------------|
| `algoritmo`, `parametro_variado`, `semente` | identificam a Réplica |
| `num_participantes`, `latencia_rede` | Participantes e atraso de ida em ms, aplicado em cada sentido |
| `ordem`, `aquecimento`, `transacao` | posição na Réplica, 1 nas de aquecimento, e o id |
| `decisao` | COMMIT ou ABORT |
| `latencia_commit_s` | do `POST /transacao` até a resposta, com a Decisao já no Log_Duravel |
| `mensagens` | eventos `envia` da transação nos logs de todos os Nós |
| `duracao_replica_s` | duração das transações da Réplica, sem subir e derrubar a pilha |

`bateria_2pc_recuperacao.csv`, uma linha por crash do Coordenador:

| Coluna | Significado |
|:-------|:------------|
| `algoritmo`, `parametro_variado`, `semente` | identificam a Réplica |
| `num_participantes`, `latencia_rede`, `transacao` | como acima |
| `em_duvida` | Participantes em dúvida logo depois do crash |
| `decisao` | Decisao do Coordenador depois de reler o Log_Duravel |
| `tempo_recuperacao_s` | do `docker compose start` até todos os Participantes decidirem |
| `mensagens`, `duracao_replica_s` | como acima, com o crash e o reinício |

Os logs dos Nós de cada Réplica do 2PC ficam em `bateria_2pc_logs/`, e o
`scripts/verificar.py` os confere junto com os dos cenários.

### Logs dos cenários

Os cenários e demos sobre o compose coletam, por Nó, o Log_Estruturado (JSONL) e,
no 2PC, o Log_Duravel, em subdiretórios como `cenario_anel_falha_logs/` e
`cenario_2pc_crash-coord_logs/`, mais um `.json` com o registro do cenário (que
inclui o relatório da verificação) e um `.txt` por Nó com o stdout do contêiner.
São esses logs que `scripts/verificar.py` consome.

## Reprodutibilidade

Cada linha de resultado e cada registro de cenário **carimba o ambiente de
execução** (hardware, SO, versões de Python e das dependências) por meio de
`src/experimentos/ambiente.py`, que faz uma coleta de leitura pura e memoizada.
O README não fixa as especificações de uma máquina de propósito: o que importa é
que **cada resultado traz o ambiente em que foi medido**, já que resultados
absolutos de máquinas diferentes não são comparados diretamente.

Os CSVs e os JSON versionados em `results/` são os dados de onde saem as tabelas
de `docs/`. Num checkout fresco, sem rodar experimento nenhum, o comando abaixo
regenera cada tabela a partir deles e sai 0:

```bash
python scripts/analisar.py conferir
```

Para refazer os experimentos, a sequência abaixo regenera **dados, tabelas e
figuras** e deixa o compose limpo:

```bash
docker compose build                               # (1) imagem única dos Nós
python scripts/bateria.py --algoritmo anel         # (2) bateria do Anel (num_nos), em processo
python scripts/bateria.py --algoritmo 2pc          #     bateria do 2PC nos Nós reais, cerca de 10 min
python scripts/analisar.py tudo                    # (3) tabelas e figuras de docs/
python scripts/analisar.py conferir                # (4) docs contra os dados novos
python scripts/cenario.py anel falha               # (5) cenário real + logs coletados
python scripts/cenario.py 2pc crash-coord          #     (ambos verificam ao final)
python scripts/verificar.py                        # (6) invariantes sobre results/
docker compose down -v --remove-orphans            # (7) teardown limpo, sem órfãos
```

A bateria do Anel, a análise e a verificação rodam **sem Docker**. A bateria do
2PC, os cenários e o teardown usam o compose. As tabelas de `docs/` são geradas
pelo passo (3) e nada é digitado à mão. A bateria do Anel é determinística por
Semente e reproduz o CSV versionado byte a byte. A do 2PC mede tempo de parede,
que varia entre execuções. Uma bateria nova muda os números, o `git diff` mostra
a diferença nos CSVs, e o passo (4) aponta as tabelas do 2PC como defasadas até a
saída do passo (3) ser colada de novo. `git checkout -- results/` volta aos dados
publicados.

## Documentos de correção

Em [`docs/`](docs/):

- [`docs/modelo-e-correcao.md`](docs/modelo-e-correcao.md) — o modelo do sistema de
  cada algoritmo (modelo temporal, canais, falhas, hipóteses de relógio,
  persistência e reinício) e as propriedades de *safety* e *liveness*, cada uma
  ligada ao invariante que a testa.
- [`docs/invariantes.md`](docs/invariantes.md) — os cinco invariantes, com a
  tolerância de cada um e a distinção entre reprova (safety violada) e aviso.
- [`docs/resultados-anel.md`](docs/resultados-anel.md) e
  [`docs/resultados-2pc.md`](docs/resultados-2pc.md) — as tabelas e figuras da
  análise, todas geradas por comando.

## Verificação

```bash
python scripts/verificar.py           # os invariantes de correção sobre results/
python scripts/analisar.py conferir   # tabelas de docs/ contra os dados atuais
python -m pytest -m "not slow"        # a suíte completa sem Docker
python -m pytest -m slow              # os testes que sobem o compose (precisam de Docker)
```

Os dois primeiros saem com código diferente de zero quando algo não bate, então
servem em sequência depois de qualquer regeneração dos dados. A suíte sem Docker
cobre as Máquinas puras, o Transporte, as falhas, a orquestração em processo, a
análise e os invariantes; a suíte `slow` sobe o compose de ponta a ponta e é
pulada de forma limpa quando o daemon não está acessível. Rode os testes `slow`
isolados — stacks de compose em paralelo podem conflitar no `--wait` e nas portas.

## Limitação do ambiente

Contêineres em uma única máquina são **Nós lógicos, não domínios de falha
independentes**: compartilham kernel, relógio e recursos, então um crash
simulado não é um crash físico, e latências medidas em segundos de relógio não
são portáveis entre máquinas. A bateria do Anel mede tempo em **passos lógicos**
de entrega, reproduzíveis dígito a dígito. A do 2PC mede **segundos de relógio**
nos Nós reais, que só se comparam entre níveis de uma mesma máquina, e cada linha
carimba o ambiente. A discussão completa dessa
hipótese e do que ela implica para a correção está em
[`docs/modelo-e-correcao.md`](docs/modelo-e-correcao.md).

## Divisão de trabalho da dupla

O grafo de dependências permite duas frentes independentes depois do núcleo
comum. A divisão abaixo mapeia as frentes às tarefas do plano de implementação;
os nomes são genéricos porque o repositório é entregue pela dupla, que preenche a
autoria conforme o histórico de commits.

**Integrante A — frente da Eleição em Anel:**

- Máquina_de_Estados pura do Anel e a regra de salto por Sucessor falho;
- Nó do Anel como app FastAPI, Dockerfile e a seção do Anel no compose;
- análise de convergência × número de Nós e os invariantes do Anel (unicidade do
  Líder por rodada, convergência dos vivos, eleito é o maior id vivo).

**Integrante B — frente do Two-Phase Commit:**

- Máquina_de_Estados pura do 2PC, o Log_Duravel write-ahead e a recuperação;
- Coordenador e Participantes como apps FastAPI e a seção do 2PC no compose, com
  um volume por Participante;
- análise de Latencia_de_Commit e Tempo_de_Recuperacao e os invariantes do 2PC
  (atomicidade e durabilidade).

**Em conjunto (camadas compartilhadas):** o núcleo comum e o Transporte com
Deadline, a injeção de falhas de aplicação e de rede com Toxiproxy, a orquestração
de experimentos, os cenários de ponta a ponta e este fechamento de
reprodutibilidade.

O relatório IEEE (duas colunas, no máximo seis páginas) e o vídeo de demonstração
(cerca de cinco minutos) são produzidos pela dupla e estão fora do escopo do
código deste repositório. Os cenários de falha (`anel falha` e `2pc crash-coord`)
são os mesmos que o vídeo mostra.

## Uso de IA generativa

Usamos o Kiro como apoio na construção do código e na revisão da escrita e da
estruturação da documentação deste repositório. Toda a análise, discussões e
resultados foram gerados integralmente pelo grupo, e todo o conteúdo foi
devidamente revisado pela equipe.
