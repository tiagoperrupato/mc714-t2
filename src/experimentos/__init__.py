"""Orquestração de experimentos: a bateria, os runners por Réplica e o compose.

O Anel e o Bully rodam em processo sobre as Máquinas_de_Estados puras, determinísticos por
Semente. O 2PC roda nos Nós reais, no compose com o Toxiproxy. O `conteineres` sobe e sonda o
compose de cada algoritmo e o da integração. A camada de análise (`src/analise/`) nunca importa
este pacote, e este pacote nunca importa a análise.
"""
