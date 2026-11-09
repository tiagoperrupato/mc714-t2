# Resultados: Eleição de Líder em Anel

A análise lê o CSV da bateria do Anel (`results/bateria_anel_num_nos.csv`) sem
reexecutar nenhuma Réplica nem subir contêiner (Requisito 11.1). Nenhuma tabela
abaixo é digitada à mão: toda tabela é produto de um comando da análise
(Requisito 11.5), e `python scripts/analisar.py conferir` reprova se alguma ficar
defasada em relação aos dados atuais.

## Convergência × número de Nós

Para cada número de Nós, a tabela traz quantas Réplicas convergiram, a média e o
IC 95% do Tempo_de_Convergencia (em passos lógicos de entrega de Mensagem) e do
número de Mensagens por Rodada_de_Eleicao. O tempo é lógico, e não em segundos de
relógio, para ser reproduzível dígito a dígito entre máquinas (limitação de
ambiente do Requisito 13.7).

| Nós | n | Convergiu | Tempo médio (passos) | IC 95% (±) | Mensagens médias | IC 95% msgs (±) |
|:----|--:|----------:|---------------------:|-----------:|-----------------:|----------------:|
| 3   | 5 |   100,0 % |                 5,00 |       0,00 |             5,00 |            0,00 |
| 5   | 5 |   100,0 % |                 9,20 |       0,56 |             9,20 |            0,56 |
| 7   | 5 |   100,0 % |                13,40 |       0,68 |            13,40 |            0,68 |

> Tabela gerada por `python scripts/analisar.py anel` a partir de `results/bateria_anel_num_nos.csv`. Não editar à mão: cole novamente a saída do comando.

![Convergência da Eleição em Anel × número de Nós](figuras/anel_convergencia.png)
