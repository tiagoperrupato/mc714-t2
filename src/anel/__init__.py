"""Algoritmo de Eleição de Líder em Anel.

A `maquina.py` é a Máquina_de_Estados pura e nunca importa FastAPI nem httpx, então os testes
a exercitam por chamadas diretas, sem rede nem contêiner. O `no.py` é o adaptador fino de rede,
que delega toda decisão à `maquina.py` e despacha pelo Transporte.
"""
