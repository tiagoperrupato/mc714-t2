# Eleição em Anel e Two-Phase Commit (MC714, Trabalho 2)

Implementação dos dois algoritmos distribuídos do Trabalho 2 como **Nós
independentes que conversam por HTTP/JSON sobre Docker Compose**: a **Eleição de
Líder em Anel**, com cinco Nós formando o anel 1 → 2 → 3 → 4 → 5 → 1, e o
**Two-Phase Commit (2PC)**, com um Coordenador e três Participantes, cada um com
seu Log_Duravel. Os dois algoritmos sofrem falhas injetadas na aplicação (crash,
pause, delay, drop) e na rede, pelo Toxiproxy (latência e partição), e cada
execução registra a Semente e o ambiente para ser reproduzível.

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
  invariantes e a bateria do Anel rodam **sem Docker**. A bateria do 2PC, os
  cenários e as demos sobre o compose (`scripts/cenario.py`, `scripts/demo.py`,
  `scripts/demo_rede_toxiproxy.py`) e as falhas de rede de `scripts/rede.py` o
  exigem.
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

Nenhuma dependência implementa eleição nem commit atômico. FastAPI, uvicorn e
httpx só transportam JSON, e a lógica dos dois algoritmos está em
`src/anel/maquina.py` e `src/doispc/maquina.py`.

## Instalação

Na raiz do projeto, a pasta do `compose.yaml`:

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

As CLIs se dividem em três grupos, **experimentar** (gerar dados), **analisar** e
**verificar**, além dos **cenários** e **demos** que sobem o compose e das
**falhas de rede** aplicadas sobre ele. As que rodam como `python scripts/...`
resolvem `src` a partir da raiz do projeto e rodam de qualquer diretório.
`scripts.rede` e `scripts.demo_rede_toxiproxy` rodam como módulo (`python -m`), a
partir da raiz do projeto.

### 1. Bateria de experimentos (`scripts/bateria.py`)

Executa Réplicas com Sementes independentes variando **um** parâmetro em pelo
menos três níveis e grava CSVs em `results/`. A do Anel roda **em processo**,
dirigindo a Máquina_de_Estados pura, e é determinística por Semente. A do 2PC
mede os Nós reais: cada Réplica sobe o compose com o Toxiproxy entre o
Coordenador e os Participantes, aplica a latência do nível e derruba tudo no fim.

```bash
python scripts/bateria.py --algoritmo anel    # varia num_nos (3, 5, 7)
python scripts/bateria.py --algoritmo 2pc     # varia latencia_rede (0, 10, 25, 50 ms), cerca de 10 min
```

Para o Anel, as métricas são Tempo_de_Convergencia (em passos lógicos de entrega
de Mensagem) e número de Mensagens. Para o 2PC, são a latência de commit vista
pelo cliente, as mensagens por transação e o tempo de recuperação depois de um
crash do Coordenador. A bateria valida a configuração inteira antes de
sobrescrever resultados, carimba a Semente e o ambiente em cada linha, e uma
Réplica que falha é registrada sem interromper o restante.

| Opção | Padrão | Descrição |
|:---|:---|:---|
| `--algoritmo` | n/d (obrigatória) | `anel` ou `2pc` |
| `--replicas` | 5 | Réplicas por nível |
| `--semente-base` | 42 | Semente base das Réplicas |
| `--transacoes` | 40 | 2pc: transações medidas por Réplica |
| `--aquecimento` | 5 | 2pc: transações de aquecimento por Réplica, gravadas e marcadas |
| `--results` | `results/` | diretório de saída |

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

Roda os invariantes de correção (safety e liveness) sobre os Logs_Estruturados e
Logs_Duraveis que os Nós reais deixaram sob `results/`. Cada diretório `*_logs` é uma
execução e é conferido sozinho, porque os invariantes do Anel tomam como vivos os Nós
que aparecem no log dela. Um caminho dado na linha de comando também é conferido
sozinho, a menos que contenha diretórios `*_logs`, e aí cada um deles é.

```bash
python scripts/verificar.py                                   # cada diretório *_logs de results/
python scripts/verificar.py results/cenario_anel_falha_logs   # um diretório só
```

Cada diretório tem o seu relatório, e a última linha diz quantos reprovaram. O comando
sai 1 quando algum invariante reprova em algum diretório. Um aviso, como o
Estado_Em_Duvida bloqueante do 2PC enquanto o Coordenador está fora, aparece no
relatório sem mudar o código de saída, porque bloquear é comportamento correto do
algoritmo. A ausência de logs também não reprova. A lista de invariantes, com a
tolerância de cada um e a razão dela, está em [`docs/invariantes.md`](docs/invariantes.md).

### 4. Cenários sobre o compose

Enquanto a bateria mede tendência com muitas Réplicas, estes scripts sobem um
cenário **real** sobre o Docker Compose, conduzem o algoritmo pelos endpoints de
controle, coletam os logs dos Nós para `results/` e rodam os invariantes sobre
eles. Cada cenário confere o desfecho contra o esperado e sai 1 quando ele
difere, quando um invariante reprova ou quando um invariante que o cenário
demonstra fica sem caso verificado nos logs. Todos derrubam o compose ao final
(`down -v --remove-orphans`) e, sem Docker, saem com uma mensagem clara em vez
de travar.

```bash
python scripts/cenario.py anel normal        # eleição converge para o maior id (5)
python scripts/cenario.py anel falha         # mata o Líder (5) e reelege o maior vivo (4)
python scripts/cenario.py anel reinicio      # mata o 5, reelege 4, reinicia o 5 e a eleição seguinte o reintegra
python scripts/cenario.py 2pc commit         # todos votam SIM -> COMMIT atômico
python scripts/cenario.py 2pc abort          # Participante 3 vota NAO -> ABORT atômico
python scripts/cenario.py 2pc crash-coord    # Coordenador cai após decidir -> dúvida, com aviso na verificação -> reinício -> COMMIT
python scripts/cenario.py 2pc particao       # isola o Participante 11 -> ABORT no prazo -> cura -> ABORT também no 11
```

As duas demos também sobem o compose:

```bash
python scripts/demo.py                    # encadeia anel falha e 2pc crash-coord e verifica os logs
python -m scripts.demo_rede_toxiproxy     # mede a latência pelo proxy, mostra a suspeita falsa e a partição do Líder
```

### 5. Falhas de rede com Toxiproxy (`scripts/rede.py`)

Cria os proxies entre os Nós, aplica latência e particiona, de forma scriptada e
repetível, falando com o HTTP API do Toxiproxy (publicado em `127.0.0.1:8474`).
Supõe o compose já no ar com o override de roteamento `compose.toxiproxy.yaml`.

```bash
docker compose -f compose.yaml -f compose.toxiproxy.yaml up -d --wait
python -m scripts.rede anel semear                         # cria os 5 proxies do Anel
python -m scripts.rede anel latencia --ms 300              # atrasa as respostas em 300 ms, abaixo do Deadline de 1 s
python -m scripts.rede anel latencia --ms 1500 --no 3      # acima do Deadline: numa eleição, o 2 salta o 3 vivo
python -m scripts.rede anel particionar --no 5             # nada mais chega ao 5, e a próxima eleição o salta
python -m scripts.rede anel curar                          # remove toxics e reativa os proxies
python -m scripts.rede doispc semear
python -m scripts.rede doispc particionar --participante 1 # corta as Mensagens do Coordenador ao Participante 11
```

Desativar o proxy de um Nó corta só as Mensagens que chegam a ele. Isso o isola
nos dois algoritmos, porque um Nó só envia em resposta ao que recebe
([`docs/modelo-e-correcao.md`](docs/modelo-e-correcao.md), Falhas).

## Docker Compose

Todos os Nós compartilham a **mesma imagem** (ver [`Dockerfile`](Dockerfile)):
o algoritmo de cada contêiner é escolhido pelo `command` do serviço, não por uma
imagem dedicada. A topologia é toda por variável de ambiente, nunca embutida no
código.

- **Seção Eleição em Anel:** `no-anel-1` .. `no-anel-5` na rede dedicada
  `rede-anel`, publicados em `8001..8005`. Cada Nó conhece o Anel e o mapa de
  URLs por ambiente e só difere no `NO_ID`.
- **Seção 2PC:** `coordenador` (publicado em `8000`) e `participante-1..3`
  (`8011..8013`) na rede `rede-2pc`. Cada Nó do 2PC monta um **volume nomeado
  próprio** em `/dados` para o seu Log_Duravel, o que preserva a Decisao através
  de reinícios. Os Deadlines de Voto e de Decisao são configuráveis por ambiente.
- **Serviço Toxiproxy:** `ghcr.io/shopify/toxiproxy:2.9.0`, ligado às duas redes,
  com o HTTP API em `8474`. Sem override, ele só fica no ar e os Nós falam direto
  entre si. Com o `compose.toxiproxy.yaml`, falam pelas escutas dele.

Comandos de ciclo de vida:

```bash
docker compose build                                   # constrói a imagem única
docker compose up -d --wait no-anel-1 no-anel-2 no-anel-3 no-anel-4 no-anel-5   # sobe o Anel
docker compose up -d --wait coordenador                # sobe o 2PC (puxa os Participantes)
docker compose down -v --remove-orphans                # derruba e limpa volumes e órfãos
```

Overrides pontuais:

- `compose.toxiproxy.yaml`: faz os Nós falarem pelas escutas do Toxiproxy.
- `compose.commit.yaml`: faz o Participante 3 votar SIM, para uma transação manual
  com COMMIT em todos. O compose base o deixa votando NAO, para mostrar o ABORT
  atômico.

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
`src/doispc/participante.py`. O que orquestra experimentos é `src/experimentos/`.

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
scripts/           bateria, analisar, verificar, cenario, demo, rede e demo_rede_toxiproxy
docs/              modelo e correção, invariantes, resultados, com dados/ e figuras/
results/           dados brutos no git (CSVs, JSON e logs dos cenários), logs da bateria do 2PC fora dele
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

Gravados em `results/`. Os dados brutos da entrega ficam no git: os CSVs das
baterias e, de cada cenário, o registro JSON e os logs dos Nós. Os logs da bateria
do 2PC, com cerca de 5 MB, e o stdout dos contêineres são refeitos a cada execução
e ficam fora dele. Há dois tipos.

### CSVs da bateria

Uma linha por Réplica no Anel e uma por transação na latência do 2PC. Além das
colunas de métrica, cada linha carrega colunas de ambiente com prefixo `amb_` (SO,
versão, arquitetura, processador, número de CPUs, versão de Python e das
dependências), o carimbo de reprodutibilidade descrito abaixo. As do 2PC trazem
também as versões do Docker e do Compose e o Python da imagem.

`bateria_anel_num_nos.csv`:

| Coluna | Significado |
|:-------|:------------|
| `algoritmo`, `parametro_variado`, `semente` | identificam a Réplica |
| `num_nos`, `taxa_falhas` | parâmetros da Réplica, com a perda fixa em 0,05 |
| `tempo_convergencia` | passos lógicos de entrega até a convergência |
| `mensagens` | tentativas de entrega até a convergência, perdidas incluídas |
| `convergiu`, `lider` | resultado da eleição |

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

Os logs dos Nós de cada Réplica do 2PC ficam em `bateria_2pc_logs/`, fora do git,
e o `scripts/verificar.py` os confere como um diretório à parte.

### Logs dos cenários

Os cenários sobre o compose coletam, por Nó, o Log_Estruturado (JSONL) e,
no 2PC, o Log_Duravel, em subdiretórios como `cenario_anel_falha_logs/` e
`cenario_2pc_crash-coord_logs/`, mais um `.json` com o registro do cenário, que
inclui o relatório da verificação, e um `.txt` por Nó com o stdout do contêiner.
Os diretórios `*_logs` e o `.json` entram no git, e o stdout fica fora. São esses
logs que `scripts/verificar.py` consome, um diretório por vez.

## Reprodutibilidade

Cada linha de resultado e cada registro de cenário **carimba o ambiente de
execução** (hardware, SO, versões de Python e das dependências) por meio de
`src/experimentos/ambiente.py`, que faz uma coleta de leitura pura e memoizada.
O README não fixa as especificações de uma máquina de propósito: o que importa é
que **cada resultado traz o ambiente em que foi medido**, já que resultados
absolutos de máquinas diferentes não são comparados diretamente.

Os CSVs, os JSON e os logs dos cenários versionados em `results/` são os dados de
onde saem as tabelas de `docs/` e o veredito dos invariantes. Num checkout fresco,
sem rodar experimento nenhum, os dois comandos abaixo regeneram cada tabela e
refazem a verificação de cada cenário, e saem 0:

```bash
python scripts/analisar.py conferir
python scripts/verificar.py
```

Para refazer os experimentos, a sequência abaixo regenera **dados, tabelas e
figuras** e deixa o compose limpo:

```bash
docker compose build                               # (1) imagem única dos Nós
python scripts/bateria.py --algoritmo anel         # (2) bateria do Anel (num_nos), em processo
python scripts/bateria.py --algoritmo 2pc          #     bateria do 2PC nos Nós reais, cerca de 10 min
python scripts/analisar.py tudo                    # (3) tabelas e figuras de docs/
python scripts/analisar.py conferir                # (4) docs contra os dados novos
python scripts/cenario.py anel normal              # (5) cenários reais + logs coletados
python scripts/cenario.py anel falha               #     (cada um verifica ao final)
python scripts/cenario.py anel reinicio
python scripts/cenario.py 2pc commit
python scripts/cenario.py 2pc abort
python scripts/cenario.py 2pc crash-coord
python scripts/cenario.py 2pc particao
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

- [`docs/README.md`](docs/README.md): o índice que leva cada item do enunciado ao
  documento, ao módulo e ao comando que o respondem.
- [`docs/modelo-e-correcao.md`](docs/modelo-e-correcao.md): o modelo do sistema de
  cada algoritmo (modelo temporal, canais, falhas, hipóteses de relógio,
  persistência e reinício) e as propriedades de *safety* e *liveness*, cada uma
  ligada ao invariante que a testa.
- [`docs/invariantes.md`](docs/invariantes.md): os seis invariantes, com a
  tolerância de cada um e a distinção entre reprovação, aviso e lacuna.
- [`docs/resultados-anel.md`](docs/resultados-anel.md) e
  [`docs/resultados-2pc.md`](docs/resultados-2pc.md): as tabelas e figuras da
  análise, todas geradas por comando.
- [`docs/relatorio/`](docs/relatorio/): o relatório IEEE, com o `.tex`, a classe
  IEEEtran e o PDF compilado.
- [`docs/roteiro-video.md`](docs/roteiro-video.md): o roteiro do vídeo de
  demonstração, com os cenários `anel falha` e `2pc crash-coord`.

## Verificação

```bash
python scripts/verificar.py           # os invariantes sobre cada diretório de logs de results/
python scripts/analisar.py conferir   # tabelas de docs/ contra os dados atuais
python -m pytest -m "not slow"        # a suíte completa sem Docker
python -m pytest -m slow              # os testes que sobem o compose (precisam de Docker)
```

Os dois primeiros saem com código diferente de zero quando algo não bate, então
servem em sequência depois de qualquer regeneração dos dados. A suíte sem Docker
cobre as Máquinas puras, o Transporte, as falhas, a orquestração em processo, a
análise e os invariantes. A suíte `slow` sobe o compose de ponta a ponta e é
pulada quando o daemon não está acessível. Rode os testes `slow` isolados, porque
stacks de compose em paralelo podem conflitar no `--wait` e nas portas.

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

**Pietro Fernandes Magaldi** fez a implementação inicial:

- o núcleo comum, o Transporte HTTP/JSON com Deadline e a conteinerização;
- a Eleição em Anel e o 2PC sobre a rede, com o Log_Duravel;
- a injeção de falhas de aplicação e de rede, esta com o Toxiproxy;
- a orquestração de experimentos, a análise, os invariantes, os cenários de ponta
  a ponta e a primeira versão dos documentos.

**Tiago Perrupato Antunes** fez a revisão e as correções que vieram depois:

- a revisão do 2PC e do Anel contra o enunciado e as aulas;
- no 2PC, os envios fora do caminho da resposta HTTP, as várias transações com id
  único, a recuperação do Coordenador com retransmissão da DECISAO e o invariante
  de validade;
- no Anel, a rodada única, a reintegração de Nó e o reenvio antes do salto;
- a bateria do 2PC nos Nós reais, a comparação com a complexidade esperada, os
  dados brutos versionados e a documentação de entrega.

O histórico de commits registra o detalhe de cada parte.

## Uso de IA generativa

Usamos dois assistentes de IA generativa. O Kiro apoiou a construção do código e a
estruturação da documentação na implementação inicial. O Claude Code apoiou a
revisão do código contra o enunciado, as correções, os experimentos e a revisão da
escrita. As decisões, a arquitetura e as validações são da dupla, que revisou todo
o conteúdo.
