# Índice do enunciado

Uma linha por item do enunciado do Trabalho 2, com o documento que o responde, o módulo que o
implementa e o comando que o demonstra ou regenera. Os IDs numeram os itens na ordem das seções do
enunciado: G para a descrição geral, I para a escolha dos algoritmos, M para o modelo e a correção,
C para a comunicação, A para o ambiente, E para a análise experimental, D para os entregáveis e B
para o bônus. Os comandos rodam na raiz do projeto, a pasta do `compose.yaml`.

## Descrição geral

| Item | O que pede | Onde está |
|:-----|:-----------|:----------|
| G1 | algoritmos clássicos corretos, com hipóteses explícitas, sob variação de carga, topologia e falhas | [`../README.md`](../README.md), [`modelo-e-correcao.md`](modelo-e-correcao.md) |
| G2 | trabalho em dupla | [`../README.md`](../README.md), seção Divisão de trabalho da dupla |
| G3 | uma máquina com contêineres como ambiente de referência | [`../README.md`](../README.md), seções Docker Compose e Limitação do ambiente |
| G6 | prazo e uma submissão por dupla no Classroom | não se aplica ao repositório |

## Implementação

| Item | O que pede | Resposta | Módulo | Comando |
|:-----|:-----------|:---------|:-------|:--------|
| I1 | dois algoritmos complementares | [`../README.md`](../README.md), [`modelo-e-correcao.md`](modelo-e-correcao.md) | [`src/anel/`](../src/anel/), [`src/doispc/`](../src/doispc/) | `python scripts/demo.py` |
| I2 | algoritmo fora da lista exige acordo prévio | o anel e o 2PC estão na lista do enunciado | n/d | n/d |
| I3 | biblioteca não implementa o algoritmo | [`../README.md`](../README.md), seção Requisitos | [`src/comum/rede.py`](../src/comum/rede.py) | n/d |
| M1 a M4 | modelo temporal, canais, falhas, relógios, persistência e reinício | [`modelo-e-correcao.md`](modelo-e-correcao.md), seções Modelo de cada algoritmo | n/d | n/d |
| M5 | propriedades de safety e liveness | [`modelo-e-correcao.md`](modelo-e-correcao.md), seções Propriedades | [`src/analise/invariantes.py`](../src/analise/invariantes.py) | `python scripts/verificar.py` |
| M6 | correção por testes ou por verificação dos logs | [`invariantes.md`](invariantes.md), [`modelo-e-correcao.md`](modelo-e-correcao.md), Falhas do modelo e onde são demonstradas | [`src/analise/invariantes.py`](../src/analise/invariantes.py), [`tests/`](../tests/) | `python scripts/verificar.py`, `python -m pytest` |
| M7 | quantidade mínima de nós | [`modelo-e-correcao.md`](modelo-e-correcao.md), itens Nós | [`compose.yaml`](../compose.yaml) | n/d |
| C1 | deadline explícito em toda chamada | [`modelo-e-correcao.md`](modelo-e-correcao.md), Canais (M2) | [`src/comum/rede.py`](../src/comum/rede.py) | n/d |
| C2, C3 | filas, pub/sub e Redis | não se aplica, a comunicação é HTTP/JSON ponto a ponto | n/d | n/d |
| A1, A2 | Compose v2, um contêiner por nó, comandos de construir, iniciar, testar e remover | [`../README.md`](../README.md), seções Docker Compose e Verificação | [`compose.yaml`](../compose.yaml), [`Dockerfile`](../Dockerfile) | `docker compose build`, `docker compose down -v --remove-orphans` |
| A3 | Docker Desktop, Docker Engine ou Podman no desenvolvimento | [`../README.md`](../README.md), seção Requisitos | n/d | n/d |
| A4 | mesma implementação, troca só de configuração | [`../README.md`](../README.md), seção Docker Compose, topologia por variável de ambiente | [`src/comum/config.py`](../src/comum/config.py) | n/d |
| A5 | ao menos três nós lógicos | [`modelo-e-correcao.md`](modelo-e-correcao.md), itens Nós | [`compose.yaml`](../compose.yaml) | n/d |
| A6 | contêineres não são domínios de falha independentes | [`modelo-e-correcao.md`](modelo-e-correcao.md), Limitação do ambiente | n/d | n/d |
| A7 | falhas de aplicação e de rede repetíveis por semente | [`modelo-e-correcao.md`](modelo-e-correcao.md), Falhas | [`src/comum/falhas.py`](../src/comum/falhas.py), [`src/comum/toxiproxy.py`](../src/comum/toxiproxy.py) | `python -m scripts.rede`, `python scripts/cenario.py` |

## Análise experimental

| Item | O que pede | Resposta | Módulo | Comando |
|:-----|:-----------|:---------|:-------|:--------|
| E1 | cenário normal e cenário de falha por algoritmo | [`modelo-e-correcao.md`](modelo-e-correcao.md), Cenários que exercitam cada propriedade | [`scripts/cenario.py`](../scripts/cenario.py) | `python scripts/cenario.py anel normal`, `python scripts/cenario.py anel falha`, `python scripts/cenario.py anel reinicio`, `python scripts/cenario.py 2pc commit`, `python scripts/cenario.py 2pc crash-coord`, `python scripts/cenario.py 2pc particao` |
| E2 | métrica de desempenho justificada | [`resultados-anel.md`](resultados-anel.md), [`resultados-bully.md`](resultados-bully.md), [`resultados-2pc.md`](resultados-2pc.md), [`modelo-e-correcao.md`](modelo-e-correcao.md), Relógios (M4) | [`src/experimentos/runner_anel.py`](../src/experimentos/runner_anel.py), [`src/experimentos/runner_bully.py`](../src/experimentos/runner_bully.py), [`src/experimentos/condutor_doispc.py`](../src/experimentos/condutor_doispc.py) | `python scripts/bateria.py --algoritmo anel`, `python scripts/bateria.py --algoritmo bully`, `python scripts/bateria.py --algoritmo 2pc` |
| E3 | um parâmetro em três ou mais níveis | seções Configuração de [`resultados-anel.md`](resultados-anel.md), [`resultados-bully.md`](resultados-bully.md) e [`resultados-2pc.md`](resultados-2pc.md) | [`scripts/bateria.py`](../scripts/bateria.py) | `python scripts/bateria.py --algoritmo anel`, `--algoritmo bully`, `--algoritmo 2pc` |
| E4 | repetição, tendência, variabilidade e percentis | tabelas de [`resultados-anel.md`](resultados-anel.md), [`resultados-bully.md`](resultados-bully.md) e [`resultados-2pc.md`](resultados-2pc.md) | [`src/analise/estatistica.py`](../src/analise/estatistica.py) | `python scripts/analisar.py tudo` |
| E5 | duração, aquecimento, hardware, SO, versões e configuração | seções Configuração dos resultados, colunas `amb_*` dos CSVs | [`src/experimentos/ambiente.py`](../src/experimentos/ambiente.py), [`src/experimentos/conteineres.py`](../src/experimentos/conteineres.py) | n/d |
| E6 | resultados contra a complexidade esperada | seções de complexidade de [`resultados-anel.md`](resultados-anel.md), [`resultados-bully.md`](resultados-bully.md) e [`resultados-2pc.md`](resultados-2pc.md), e a comparação Anel × Bully de [`resultados-bully.md`](resultados-bully.md) | [`src/analise/anel_exp.py`](../src/analise/anel_exp.py), [`src/analise/bully_exp.py`](../src/analise/bully_exp.py), [`src/analise/doispc_exp.py`](../src/analise/doispc_exp.py) | `python scripts/analisar.py anel-complexidade`, `python scripts/analisar.py bully`, `python scripts/analisar.py bully-anel`, `python scripts/analisar.py 2pc-complexidade` |
| E7 | experimentos automatizados e reproduzíveis | [`../README.md`](../README.md), seção Reprodutibilidade | [`scripts/`](../scripts/) | `python scripts/analisar.py conferir` |

## Entregáveis

| Item | O que pede | Onde está |
|:-----|:-----------|:----------|
| G4 | PDF no Classroom com nome e RA da dupla e os links do repositório e do vídeo na primeira página | n/d |
| G5, D2 | código, testes, `compose.yaml`, instruções, scripts e dados brutos | este repositório. Os dados brutos são os CSVs, os JSON e os logs dos cenários (`cenario_*_logs/`) de [`../results/`](../results/) |
| D1 | relatório IEEE de até seis páginas | n/d |
| D2 | cópia do relatório e tag de entrega | [`relatorio/`](relatorio/), com o `.tex`, a classe e o PDF |
| D3 | vídeo de cerca de cinco minutos | n/d |

## Bônus

Uma linha por conceito adicional do B1.

| Item | O que pede | Resposta | Módulo | Comando |
|:-----|:-----------|:---------|:-------|:--------|
| B1 | falhas combinadas | [`modelo-e-correcao.md`](modelo-e-correcao.md), Cenários que exercitam cada propriedade | [`scripts/cenario.py`](../scripts/cenario.py) | `python scripts/cenario.py 2pc crash-coord-part` |
| B1 | integração dos algoritmos num problema prático | [`modelo-e-correcao.md`](modelo-e-correcao.md), Integração: o Líder do Anel coordena o 2PC | [`src/integracao/`](../src/integracao/), [`compose.integracao.yaml`](../compose.integracao.yaml) | `python scripts/cenario.py integracao failover`, `python scripts/cenario.py integracao failover-isolamento` |

## Artefatos versionados

`dados/` guarda os CSVs que sustentam cada tabela dos documentos, e `figuras/` guarda os gráficos,
em PNG para os markdowns e em PDF para o relatório. Nenhuma tabela é digitada à mão, e
`python scripts/analisar.py conferir` sai 1 se algum documento ficar defasado.

| Arquivo | Origem |
|:--------|:-------|
| `dados/anel_convergencia.csv`, `figuras/anel_convergencia.{png,pdf}` | `python scripts/analisar.py anel` |
| `dados/anel_complexidade.csv` | `python scripts/analisar.py anel-complexidade` |
| `dados/doispc_latencia.csv`, `figuras/doispc_latencia.{png,pdf}` | `python scripts/analisar.py 2pc-latencia` |
| `dados/doispc_complexidade.csv` | `python scripts/analisar.py 2pc-complexidade` |
| `dados/doispc_recuperacao.csv`, `figuras/doispc_recuperacao.{png,pdf}` | `python scripts/analisar.py 2pc-recuperacao` |
| `dados/bully_complexidade.csv` | `python scripts/analisar.py bully` |
| `dados/anel_bully.csv`, `figuras/anel_bully.{png,pdf}` | `python scripts/analisar.py bully-anel` |
