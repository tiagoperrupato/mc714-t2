# Resultados: Two-Phase Commit

A bateria mede o 2PC nos Nós reais. Cada Réplica sobe o compose do zero, com o Toxiproxy entre o
Coordenador e cada Participante, aplica a latência do nível e derruba tudo no fim. A análise lê os
CSVs de `results/` sem reexecutar Réplica, e `python scripts/analisar.py conferir` reprova se alguma
tabela abaixo ficar defasada. Os tempos são de relógio, valem para a máquina carimbada em cada linha
dos CSVs e mudam um pouco a cada execução da bateria.

## Configuração

- **Topologia.** Um Coordenador e três Participantes, todos votando SIM.
- **Latência de rede.** Atraso de ida de 0, 10, 25 e 50 ms, sem jitter, aplicado pelo Toxiproxy nos
  dois sentidos de cada um dos quatro proxies. Um envio do Transporte custa então dois atrasos de ida,
  mais o processamento.
- **Réplicas.** Cinco por nível. As Sementes vão de 42 a 61 na latência e de 62 a 81 na
  recuperação. A Semente entra nos ids de transação e no log de cada Nó, mas o tempo medido não
  depende dela.
- **Transações.** 45 por Réplica, em sequência. As 5 primeiras são aquecimento: ficam no CSV,
  marcadas, e as tabelas as descartam. Cada transação só começa depois de o Coordenador receber todos
  os ACK da anterior, para não herdar a fila de saída dela.
- **Prazos do compose.** 3 s para a coleta de Votos, 2 s por envio e retransmissão da DECISAO a cada
  1 s.
- **Duração e ambiente.** Cada linha traz a duração das transações da Réplica (`duracao_replica_s`) e
  o ambiente (`amb_*`), inclusive as versões do Docker e do Compose e o Python da imagem.

```bash
python scripts/bateria.py --algoritmo 2pc     # cerca de 10 min nesta máquina
python scripts/analisar.py 2pc-latencia
python scripts/analisar.py 2pc-recuperacao
```

## Latência de commit × latência de rede

A latência é a do `POST /transacao` vista pelo cliente. O Coordenador responde quando a Decisao já
está no Log_Duravel dele. Média e IC 95% saem das médias por Réplica, que são as amostras
independentes, e os percentis saem de todas as transações do nível. Mensagens é a média, por
transação, dos eventos `envia` nos logs de todos os Nós.

| Latência de rede (ms) | Réplicas | Transações | Média (ms) | IC 95% (±) | p50 (ms) | p95 (ms) | p99 (ms) | Mensagens |
|:----------------------|---------:|-----------:|-----------:|-----------:|---------:|---------:|---------:|----------:|
| 0                     |        5 |        200 |        8,5 |        2,1 |      7,1 |     13,0 |     29,3 |     12,00 |
| 10                    |        5 |        200 |       83,3 |        0,5 |     83,7 |     90,9 |    105,3 |     12,00 |
| 25                    |        5 |        200 |      189,8 |        4,4 |    189,1 |    210,4 |    245,1 |     12,00 |
| 50                    |        5 |        200 |      344,6 |        2,1 |    345,0 |    363,7 |    371,7 |     12,00 |

> Tabela gerada por `python scripts/analisar.py 2pc-latencia` a partir de `results/bateria_2pc_latencia_rede.csv`. Não editar à mão: cole novamente a saída do comando.

![Latência de commit do 2PC × latência de rede](figuras/doispc_latencia.png)

## Mensagens e latência × complexidade esperada

Cada transação sem falha custa 4N mensagens, com N Participantes. PREPARE, Voto e DECISAO são as 3N do
protocolo básico, e o ACK de cada Participante é a mensagem a mais que a retransmissão da DECISAO
exige. O caminho crítico da latência também sai do protocolo. O Coordenador envia os PREPARE um depois
do outro, e cada envio espera a resposta do Participante, uma ida e volta. O último PREPARE sai depois
de N − 1 idas e voltas e leva mais uma ida até o Participante, e o Voto dele leva outra ida de volta.
São 2N atrasos de ida ao todo, seis com N = 3. O resto do custo (`fsync`, HTTP, escalonamento) é o que
se mede com atraso zero, e a tabela compara o acréscimo sobre esse piso com os 2N atrasos.

| Latência de rede (ms) | Transações | Mensagens médias | 4N | Latência média (ms) | Acréscimo sobre 0 ms | 2N × atraso (ms) | Acréscimo / 2N × atraso |
|:----------------------|-----------:|-----------------:|---:|--------------------:|---------------------:|-----------------:|------------------------:|
| 0                     |        200 |            12,00 | 12 |                 8,5 |                  0,0 |              0,0 |                     n/d |
| 10                    |        200 |            12,00 | 12 |                83,3 |                 74,8 |             60,0 |                    1,25 |
| 25                    |        200 |            12,00 | 12 |               189,8 |                181,3 |            150,0 |                    1,21 |
| 50                    |        200 |            12,00 | 12 |               344,6 |                336,1 |            300,0 |                    1,12 |

> Tabela gerada por `python scripts/analisar.py 2pc-complexidade` a partir de `results/bateria_2pc_latencia_rede.csv`. Não editar à mão: cole novamente a saída do comando.

As mensagens batem com 4N em todas as transações, sem nenhuma retransmissão, e não dependem do atraso.
A latência cresce em linha com os 2N atrasos de ida, mas fica um pouco acima deles. Nos logs de uma
transação, cada uma das seis pernas do caminho crítico leva alguns milissegundos além do atraso,
somando o `fsync` do Voto, o repasse pelo Toxiproxy e o despacho pela fila de saída de cada Nó. Os
PREPARE em sequência fazem o custo crescer com N. Enviados em paralelo, eles deixariam o caminho
crítico em dois atrasos de ida para qualquer N.

## Tempo de recuperação × latência de rede

Em cada Réplica, o processo do Coordenador morre logo depois de gravar a Decisao, antes de difundi-la,
e os três Participantes ficam em dúvida. O condutor reinicia o contêiner e mede do
`docker compose start` até todos os Participantes reportarem a Decisao. O tempo inclui a partida do
contêiner e do processo, a releitura do Log_Duravel e a DECISAO pela rede. Com uma amostra por
Réplica, a tabela mostra mínimo e máximo, e não percentis.

| Latência de rede (ms) | Réplicas | Média (s) | IC 95% (±) | Mínimo (s) | Máximo (s) | Mensagens |
|:----------------------|---------:|----------:|-----------:|-----------:|-----------:|----------:|
| 0                     |        5 |     0,553 |      0,030 |      0,533 |      0,592 |     18,00 |
| 10                    |        5 |     0,787 |      0,349 |      0,590 |      1,262 |     18,00 |
| 25                    |        5 |     0,866 |      0,477 |      0,671 |      1,552 |     16,80 |
| 50                    |        5 |     0,817 |      0,048 |      0,789 |      0,884 |     14,00 |

> Tabela gerada por `python scripts/analisar.py 2pc-recuperacao` a partir de `results/bateria_2pc_recuperacao.csv`. Não editar à mão: cole novamente a saída do comando.

![Tempo de recuperação do 2PC × latência de rede](figuras/doispc_recuperacao.png)

A maior parte do tempo, e quase toda a variação entre Réplicas, vem da partida do contêiner e do
interpretador, o que os logs da bateria mostram (eles ficam fora do git e voltam com
`python scripts/bateria.py --algoritmo 2pc`). As mensagens passam de 12 porque o Coordenador
difunde a Decisao relida antes de o servidor HTTP dele aceitar conexões. Os ACK que chegam nessa janela
falham, e a retransmissão de 1 s repete a DECISAO e o ACK desses Participantes. Com mais atraso, menos
ACK chegam cedo demais.
