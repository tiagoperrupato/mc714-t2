"""Demo em memória: uma transação 2PC que commita e outra que aborta.

Roda a Máquina_de_Estados pura ponta a ponta, sem rede, e imprime a Decisao
final e o conteúdo do Log_Duravel de cada Participante nos dois casos. Com
`pytest -s`, a saída aparece no console; sem `-s`, a demo segue valendo como
asserção de atomicidade e de durabilidade.
"""

from __future__ import annotations

from src.doispc.log_duravel import DECISAO_ABORT, DECISAO_COMMIT, VOTO_NAO
from tests.doispc.test_maquina import COORD, PARTS, DoisPCSimulado


def _formatar_log(sim: DoisPCSimulado, no_id: int) -> str:
    """Resume o conteúdo do Log_Duravel de um Nó numa linha legível."""
    entradas = sim.logs[no_id].entradas
    partes = [f"{e.fase}={e.valor}" for e in entradas]
    return ", ".join(partes) if partes else "(vazio)"


def test_demo_transacao_commit(capsys) -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao("tx-commit")

    linhas = ["== 2PC: transação que COMMITA (todos votam SIM) =="]
    linhas.append(f"participantes: {PARTS}")
    linhas.append(f"mensagens trocadas: {len(sim.historico)}")
    linhas.append(f"Decisao do Coordenador {COORD}: {sim.estado_coordenador.decisao}")
    linhas.append(f"  Log_Duravel do Coordenador {COORD}: {_formatar_log(sim, COORD)}")
    for p in PARTS:
        linhas.append(f"  Log_Duravel do Participante {p}: {_formatar_log(sim, p)}")
    print("\n".join(linhas))

    assert sim.estado_coordenador.decisao == DECISAO_COMMIT
    assert set(sim.decisoes_participantes().values()) == {DECISAO_COMMIT}


def test_demo_transacao_abort(capsys) -> None:
    # O Participante 2 vota NAO: a transação toda deve abortar.
    sim = DoisPCSimulado(COORD, PARTS, votos_desejados={2: VOTO_NAO})
    sim.executar_transacao("tx-abort")

    linhas = ["== 2PC: transação que ABORTA (Participante 2 vota NAO) =="]
    linhas.append(f"participantes: {PARTS}")
    linhas.append(f"mensagens trocadas: {len(sim.historico)}")
    linhas.append(f"Decisao do Coordenador {COORD}: {sim.estado_coordenador.decisao}")
    linhas.append(f"  Log_Duravel do Coordenador {COORD}: {_formatar_log(sim, COORD)}")
    for p in PARTS:
        linhas.append(f"  Log_Duravel do Participante {p}: {_formatar_log(sim, p)}")
    print("\n".join(linhas))

    assert sim.estado_coordenador.decisao == DECISAO_ABORT
    assert set(sim.decisoes_participantes().values()) == {DECISAO_ABORT}
