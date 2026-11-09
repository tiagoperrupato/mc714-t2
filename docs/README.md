# Índice do enunciado

Uma linha por item do enunciado do Trabalho 2, com o documento que o responde, o módulo que o
implementa e o comando que o demonstra ou regenera. Os IDs são os do checklist do enunciado. Os
comandos rodam a partir de `T2/`.

## Implementação

| Item | O que pede | Resposta | Módulo | Comando |
|:-----|:-----------|:---------|:-------|:--------|
| I1 | dois algoritmos complementares | [`../README.md`](../README.md), [`modelo-e-correcao.md`](modelo-e-correcao.md) | [`src/anel/`](../src/anel/), [`src/doispc/`](../src/doispc/) | `python scripts/demo.py` |
| I3 | biblioteca não implementa o algoritmo | [`../README.md`](../README.md), seção Requisitos | [`src/comum/rede.py`](../src/comum/rede.py) | n/d |
| M1 a M4 | modelo temporal, canais, falhas, relógios, persistência e reinício | [`modelo-e-correcao.md`](modelo-e-correcao.md), seções Modelo de cada algoritmo | n/d | n/d |
| M5 | propriedades de safety e liveness | [`modelo-e-correcao.md`](modelo-e-correcao.md), seções Propriedades | [`src/analise/invariantes.py`](../src/analise/invariantes.py) | `python scripts/verificar.py` |
| M6 | correção por testes ou por verificação dos logs | [`invariantes.md`](invariantes.md) | [`src/analise/invariantes.py`](../src/analise/invariantes.py), [`tests/`](../tests/) | `python scripts/verificar.py`, `python -m pytest` |
| M7 | quantidade mínima de nós | [`modelo-e-correcao.md`](modelo-e-correcao.md), itens Nós | [`compose.yaml`](../compose.yaml) | n/d |
| C1 | deadline explícito em toda chamada | [`modelo-e-correcao.md`](modelo-e-correcao.md), Canais (M2) | [`src/comum/rede.py`](../src/comum/rede.py) | n/d |
| C2, C3 | filas, pub/sub e Redis | não se aplica, a comunicação é HTTP/JSON ponto a ponto | n/d | n/d |
| A1, A2 | Compose v2, um contêiner por nó, comandos de construir, iniciar, testar e remover | [`../README.md`](../README.md), seções Docker Compose e Verificação | [`compose.yaml`](../compose.yaml), [`Dockerfile`](../Dockerfile) | `docker compose build`, `docker compose down -v --remove-orphans` |
| A4 | mesma implementação, troca só de configuração | [`../README.md`](../README.md), seção Docker Compose, topologia por variável de ambiente | [`src/comum/config.py`](../src/comum/config.py) | n/d |
| A5 | ao menos três nós lógicos | [`modelo-e-correcao.md`](modelo-e-correcao.md), itens Nós | [`compose.yaml`](../compose.yaml) | n/d |
| A6 | contêineres não são domínios de falha independentes | [`modelo-e-correcao.md`](modelo-e-correcao.md), Limitação do ambiente | n/d | n/d |
| A7 | falhas de aplicação e de rede repetíveis por semente | [`modelo-e-correcao.md`](modelo-e-correcao.md), Falhas | [`src/comum/falhas.py`](../src/comum/falhas.py), [`src/comum/toxiproxy.py`](../src/comum/toxiproxy.py) | `python -m scripts.rede`, `python scripts/cenario.py` |

## Análise experimental

| Item | O que pede | Resposta | Módulo | Comando |
|:-----|:-----------|:---------|:-------|:--------|
| E1 | cenário normal e cenário de falha por algoritmo | [`modelo-e-correcao.md`](modelo-e-correcao.md), Cenários que exercitam cada propriedade | [`scripts/cenario.py`](../scripts/cenario.py) | `python scripts/cenario.py anel falha`, `python scripts/cenario.py 2pc crash-coord` |
| E2 | métrica de desempenho justificada | [`resultados-anel.md`](resultados-anel.md), [`resultados-2pc.md`](resultados-2pc.md), [`modelo-e-correcao.md`](modelo-e-correcao.md), Relógios (M4) | [`src/experimentos/runner_anel.py`](../src/experimentos/runner_anel.py), [`src/experimentos/condutor_doispc.py`](../src/experimentos/condutor_doispc.py) | `python scripts/bateria.py --algoritmo anel`, `python scripts/bateria.py --algoritmo 2pc` |
| E3 | um parâmetro em três ou mais níveis | seções Configuração de [`resultados-anel.md`](resultados-anel.md) e [`resultados-2pc.md`](resultados-2pc.md) | [`scripts/bateria.py`](../scripts/bateria.py) | `python scripts/bateria.py --algoritmo anel`, `--algoritmo 2pc` |
| E4 | repetição, tendência, variabilidade e percentis | tabelas de [`resultados-anel.md`](resultados-anel.md) e [`resultados-2pc.md`](resultados-2pc.md) | [`src/analise/estatistica.py`](../src/analise/estatistica.py) | `python scripts/analisar.py tudo` |
| E5 | duração, aquecimento, hardware, SO, versões e configuração | seções Configuração dos resultados, colunas `amb_*` dos CSVs | [`src/experimentos/ambiente.py`](../src/experimentos/ambiente.py), [`src/experimentos/conteineres.py`](../src/experimentos/conteineres.py) | n/d |
| E6 | resultados contra a complexidade esperada | seções de complexidade de [`resultados-anel.md`](resultados-anel.md) e [`resultados-2pc.md`](resultados-2pc.md) | [`src/analise/anel_exp.py`](../src/analise/anel_exp.py), [`src/analise/doispc_exp.py`](../src/analise/doispc_exp.py) | `python scripts/analisar.py anel-complexidade`, `python scripts/analisar.py 2pc-complexidade` |
| E7 | experimentos automatizados e reproduzíveis | [`../README.md`](../README.md), seção Reprodutibilidade | [`scripts/`](../scripts/) | `python scripts/analisar.py conferir` |

## Entregáveis

| Item | O que pede | Onde está |
|:-----|:-----------|:----------|
| G5, D2 | código, testes, `compose.yaml`, instruções, scripts e dados brutos | este repositório. Os dados brutos são os CSVs e os JSON de [`../results/`](../results/) |
| D2 | cópia do relatório e tag de entrega | n/d |
| D1 | relatório IEEE de até seis páginas | n/d, fora do repositório |
| D3 | vídeo de cerca de cinco minutos | n/d, fora do repositório |

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
