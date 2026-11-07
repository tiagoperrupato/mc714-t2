"""Algoritmo de Eleição de Líder em Anel.

Este pacote separa, no espírito do design, a Máquina_de_Estados pura
(`maquina.py`) do app FastAPI do Nó (`no.py`). A `maquina.py` nunca importa
FastAPI nem httpx: a lógica da eleição é exercitada por chamadas diretas nos
testes, sem subir rede nem contêiner. O `no.py` é o adaptador fino de rede, que
delega toda decisão à `maquina.py` e despacha pelo Transporte; é o único que
toca FastAPI/httpx, e ainda assim os importa de forma tardia.
"""
