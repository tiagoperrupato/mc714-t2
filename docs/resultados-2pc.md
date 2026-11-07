# Resultados: Two-Phase Commit

A análise lê o CSV da bateria do 2PC (`results/bateria_2pc_latencia_rede.csv`)
sem reexecutar nenhuma Réplica nem subir contêiner (Requisito 11.1). Nenhuma
tabela abaixo é digitada à mão: toda tabela é produto de um comando da análise
(Requisito 11.5), e `python scripts/analisar.py conferir` reprova se alguma ficar
defasada em relação aos dados atuais.

## Latência de commit × latência de rede

Para cada nível de latência de rede, a tabela traz a média e o IC 95% da
Latencia_de_Commit e seus percentis p50, p95 e p99 (Requisito 11.2). A cauda
(p95/p99) revela o pior caso que a média esconde. Os tempos são lógicos e
reproduzíveis por Semente, não segundos de relógio (Requisito 13.7).

| Latência de rede | n | Latência de commit média | IC 95% (±) | p50     | p95     | p99     |
|:-----------------|--:|-------------------------:|-----------:|--------:|--------:|--------:|
| 1,0              | 5 |                   1,7874 |     0,4468 |  1,6914 |  2,2093 |  2,2493 |
| 5,0              | 5 |                  11,1995 |     4,0436 | 10,3582 | 15,6085 | 16,4995 |
| 10,0             | 5 |                  21,6561 |    10,2207 | 18,9076 | 32,6425 | 34,3507 |

> Tabela gerada por `python scripts/analisar.py 2pc-latencia` a partir de `results/bateria_2pc_latencia_rede.csv`. Não editar à mão: cole novamente a saída do comando.

![Latência de commit do 2PC × latência de rede](figuras/doispc_latencia.png)

## Tempo de recuperação sob falha × latência de rede

O Tempo_de_Recuperacao é o custo lógico de um Participante reler seu Log_Duravel
e reconstruir a Decisao após um reinício (Requisito 6.6), medido na bateria com a
recuperação simulada. A tabela traz a média, o IC 95% e os percentis por nível de
latência de rede.

| Latência de rede | n | Recuperação média | IC 95% (±) | p50    | p95    | p99    |
|:-----------------|--:|------------------:|-----------:|-------:|-------:|-------:|
| 1,0              | 5 |            3,0000 |     0,0000 | 3,0000 | 3,0000 | 3,0000 |
| 5,0              | 5 |            3,0000 |     0,0000 | 3,0000 | 3,0000 | 3,0000 |
| 10,0             | 5 |            3,0000 |     0,0000 | 3,0000 | 3,0000 | 3,0000 |

> Tabela gerada por `python scripts/analisar.py 2pc-recuperacao` a partir de `results/bateria_2pc_latencia_rede.csv`. Não editar à mão: cole novamente a saída do comando.

![Tempo de recuperação do 2PC sob falha × latência de rede](figuras/doispc_recuperacao.png)
