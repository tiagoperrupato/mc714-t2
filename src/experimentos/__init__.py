"""Orquestração de experimentos: runners por réplica das duas frentes.

Este pacote concentra a execução de uma Réplica de cada algoritmo em processo,
sem subir contêiner, para que a bateria (`scripts/bateria.py`) seja determinística
por Semente e testável no CI. Os runners dirigem as Máquinas_de_Estados puras do
Anel e do 2PC com um modelo de rede semeado (drop/latência), medindo as métricas
do Requisito 10: Tempo_de_Convergencia e número de Mensagens no Anel;
Latencia_de_Commit, vazão e Tempo_de_Recuperacao no 2PC.

A fronteira entre camadas é mantida: estes runners importam as Máquinas_de_Estados, mas a
camada de análise (`src/analise/`) nunca os importa, e eles nunca importam a
análise.
"""
