"""Demo em memória: uma eleição completa num Anel de 5 Nós.

Roda a Máquina_de_Estados pura ponta a ponta, sem rede, e imprime o Líder
eleito e as Mensagens trocadas. Com `pytest -s`, a saída é visível no console;
sem `-s`, o teste segue valendo como asserção de convergência.
"""

from __future__ import annotations

from tests.anel.test_maquina import AnelSimulado


def test_demo_eleicao_anel_5_nos(capsys) -> None:
    anel = (1, 2, 3, 4, 5)
    sim = AnelSimulado(anel)
    sim.disparar(iniciador=1)

    linhas = ["== Eleição em Anel de 5 Nós (iniciador = 1) =="]
    linhas.append(f"topologia (sucessor cíclico): {anel}")
    linhas.append(f"mensagens trocadas: {len(sim.historico)}")
    for i, m in enumerate(sim.historico, start=1):
        if m.tipo == "eleicao":
            detalhe = f"ids acumulados = {list(m.corpo['ids'])}"
        else:
            detalhe = f"lider anunciado = {m.corpo['lider']}"
        linhas.append(f"  {i:2d}. {m.tipo:8s} {m.origem} -> {m.destino}  ({detalhe})")

    lideres = sim.lideres()
    linhas.append(f"Líder eleito por Nó: {lideres}")
    print("\n".join(linhas))

    # A demo também é uma asserção: todos os Nós vivos convergem para o maior id.
    assert set(lideres.values()) == {5}
    assert len(sim.historico) > 0
