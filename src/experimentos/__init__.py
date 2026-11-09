"""Orquestração de experimentos: a bateria, os runners por Réplica e o compose.

O Anel roda em processo sobre a Máquina_de_Estados pura, determinístico por Semente. O 2PC roda nos
Nós reais, no compose com o Toxiproxy. A camada de análise (`src/analise/`) nunca importa este
pacote, e este pacote nunca importa a análise.
"""
