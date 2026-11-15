"""Testes do controlador de falhas de aplicação (`src.comum.falhas`).

Cobrem, sem Docker:

- **Determinismo por Semente**: com a mesma Semente e o mesmo
  Cenário (mesma sequência de injeções), a sequência de decisões de drop e de
  delay é idêntica entre duas execuções independentes; Sementes diferentes
  produzem sequências diferentes.
- **Independência dos streams**: consumir o stream de drop
  não desloca o de delay, e vice-versa.
- **Modos de falha**: crash levanta `NoEmCrashError` nas duas
  direções; pause congela uma vez e depois resume; o `ClienteComFalhas` suprime
  envios sob crash/drop e delega os demais.
- **Registro do Plano_de_Falhas no log**.
"""

from __future__ import annotations

import io
import json

import pytest

from src.comum.falhas import (
    ClienteComFalhas,
    ControladorFalhas,
    EnvioDescartado,
    NoEmCrashError,
    envolver_recebimento,
    registrar_plano_no_log,
)
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import EnvioOk

SEMENTE = 20260815


def _msg(i: int = 0) -> Mensagem:
    return Mensagem(tipo="eleicao", origem=1, destino=2, corpo={"n": i})


def _sem_dormir(_segundos: float) -> None:
    """Dorme instantâneo: os testes não precisam gastar tempo real de parede."""
    return None


def _sequencia_drop(semente: int, *, p: float, n: int) -> list[bool]:
    """Executa `n` decisões de drop e devolve quais foram descartadas."""
    ctrl = ControladorFalhas(semente, dormir=_sem_dormir)
    ctrl.injetar_drop(p)
    return [not ctrl.antes_de_receber(_msg(i)) for i in range(n)]


def _sequencia_delay(semente: int, *, teto: float, n: int) -> list[float]:
    """Executa `n` decisões de delay e devolve os atrasos sorteados (via stream)."""
    atrasos: list[float] = []

    def capturar(segundos: float) -> None:
        atrasos.append(segundos)

    ctrl = ControladorFalhas(semente, dormir=capturar)
    ctrl.injetar_delay(teto)
    for i in range(n):
        ctrl.antes_de_receber(_msg(i))
    return atrasos


# ---------------------------------------------------------------------------
# Determinismo por Semente
# ---------------------------------------------------------------------------


def test_drop_e_identico_entre_execucoes_com_mesma_semente() -> None:
    a = _sequencia_drop(SEMENTE, p=0.5, n=200)
    b = _sequencia_drop(SEMENTE, p=0.5, n=200)
    assert a == b
    # A probabilidade 0.5 num stream de 200 não deve degenerar em tudo-igual.
    assert 0 < sum(a) < 200


def test_delay_e_identico_entre_execucoes_com_mesma_semente() -> None:
    a = _sequencia_delay(SEMENTE, teto=0.3, n=200)
    b = _sequencia_delay(SEMENTE, teto=0.3, n=200)
    assert a == b
    assert all(0.0 <= x <= 0.3 for x in a)


def test_sementes_diferentes_produzem_sequencias_diferentes() -> None:
    a = _sequencia_drop(SEMENTE, p=0.5, n=200)
    b = _sequencia_drop(SEMENTE + 1, p=0.5, n=200)
    assert a != b


def test_stream_de_drop_nao_e_afetado_por_delay_ligado() -> None:
    # O stream de drop vem de um gerador distinto do de delay: ligar o delay em
    # paralelo não muda a sequência de descartes. Prova-se comparando a sequência
    # de descartes com e sem delay ligado — ambas idênticas.
    so_drop = _sequencia_drop(SEMENTE, p=0.5, n=100)

    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_drop(0.5)
    ctrl.injetar_delay(0.3)
    com_delay = [not ctrl.antes_de_receber(_msg(i)) for i in range(100)]

    assert com_delay == so_drop


def test_stream_de_delay_nao_e_afetado_por_drop_ligado() -> None:
    # Simetricamente: o stream de delay não é deslocado pelo stream de drop. Cada
    # Mensagem avaliada consome um valor do stream de delay (mesmo quando a
    # Mensagem acaba descartada), então a sequência de atrasos sorteados por
    # Mensagem é idêntica à do delay puro. Capturamos TODOS os atrasos sorteados
    # (não só os dormidos) via um controlador que não descarta a captura.
    so_delay = _sequencia_delay(SEMENTE, teto=0.3, n=100)

    sorteados: list[float] = []

    class _CtrlCaptura(ControladorFalhas):
        def _decidir_atraso(self) -> float:  # type: ignore[override]
            atraso = super()._decidir_atraso()
            if self._modos.atraso_ativo:
                sorteados.append(atraso)
            return atraso

    ctrl = _CtrlCaptura(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_drop(0.5)
    ctrl.injetar_delay(0.3)
    for i in range(100):
        ctrl.antes_de_receber(_msg(i))

    assert sorteados == so_delay


# ---------------------------------------------------------------------------
# Modos de falha
# ---------------------------------------------------------------------------


def test_crash_recusa_recebimento_e_envio() -> None:
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_crash()
    with pytest.raises(NoEmCrashError):
        ctrl.antes_de_receber(_msg())
    with pytest.raises(NoEmCrashError):
        ctrl.antes_de_enviar(_msg())


def test_pause_congela_uma_vez_e_resume() -> None:
    dormidas: list[float] = []
    ctrl = ControladorFalhas(SEMENTE, dormir=dormidas.append)
    ctrl.injetar_pause(0.2)
    # Primeiro evento paga a pausa; o seguinte já não.
    ctrl.antes_de_receber(_msg(0))
    ctrl.antes_de_receber(_msg(1))
    assert dormidas == [0.2]


def test_drop_desligado_nao_consome_stream() -> None:
    # Com drop desligado, nenhuma decisão é descartada e o stream não avança:
    # ligar o drop depois produz a MESMA sequência que ligá-lo desde o início.
    ctrl_tardio = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    for i in range(10):
        ctrl_tardio.antes_de_receber(_msg(i))  # drop desligado: não consome
    ctrl_tardio.injetar_drop(0.5)
    tardio = [not ctrl_tardio.antes_de_receber(_msg(i)) for i in range(50)]

    cedo = _sequencia_drop(SEMENTE, p=0.5, n=50)
    assert tardio == cedo


def test_injecoes_fora_de_dominio_sao_recusadas() -> None:
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    with pytest.raises(ValueError):
        ctrl.injetar_drop(1.5)
    with pytest.raises(ValueError):
        ctrl.injetar_delay(-1.0)
    with pytest.raises(ValueError):
        ControladorFalhas(-1)


# ---------------------------------------------------------------------------
# Plugagem no recebimento e no envio
# ---------------------------------------------------------------------------


def test_envolver_recebimento_descarta_sem_tocar_a_maquina() -> None:
    chamadas: list[Mensagem] = []

    def callback(mensagem: Mensagem):
        chamadas.append(mensagem)
        return {"ack": True}

    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_drop(1.0)  # descarta tudo
    recebedor = envolver_recebimento(ctrl, callback)
    resposta = recebedor(_msg())
    assert resposta == {"ack": False, "descartada": True}
    assert chamadas == []  # a Máquina nunca foi chamada


class _ClienteFake:
    def __init__(self) -> None:
        self.enviadas: list[Mensagem] = []

    def enviar(self, destino_url, mensagem, deadline):
        self.enviadas.append(mensagem)
        return EnvioOk(status=200, corpo={"ack": True})


def test_cliente_com_falhas_suprime_envio_sob_drop() -> None:
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_drop(1.0)
    fake = _ClienteFake()
    cliente = ClienteComFalhas(fake, ctrl)
    resultado = cliente.enviar("http://x", _msg(), 1.0)
    assert isinstance(resultado, EnvioDescartado)
    assert fake.enviadas == []  # nada chegou à rede


def test_cliente_com_falhas_suprime_envio_sob_crash() -> None:
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_crash()
    fake = _ClienteFake()
    cliente = ClienteComFalhas(fake, ctrl)
    resultado = cliente.enviar("http://x", _msg(), 1.0)
    assert isinstance(resultado, EnvioDescartado)
    assert resultado.motivo == "crash"
    assert fake.enviadas == []


def test_cliente_com_falhas_delega_quando_sem_falha() -> None:
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    fake = _ClienteFake()
    cliente = ClienteComFalhas(fake, ctrl)
    resultado = cliente.enviar("http://x", _msg(), 1.0)
    assert isinstance(resultado, EnvioOk)
    assert len(fake.enviadas) == 1


# ---------------------------------------------------------------------------
# Registro no log
# ---------------------------------------------------------------------------


def test_registrar_plano_no_log_grava_semente_e_plano() -> None:
    buffer = io.StringIO()
    log = LogEstruturado(no_id=7, destino=buffer, relogio=lambda: 0.0)
    ctrl = ControladorFalhas(SEMENTE, dormir=_sem_dormir)
    ctrl.injetar_drop(0.3)
    ctrl.injetar_delay(0.1)
    registrar_plano_no_log(ctrl, log, cenario={"algoritmo": "anel"})

    linha = json.loads(buffer.getvalue().strip())
    assert linha["tipo"] == "inicio_replica"
    assert linha["semente"] == SEMENTE
    assert linha["plano_de_falhas"]["drop_p"] == 0.3
    assert linha["plano_de_falhas"]["delay_s"] == 0.1
    assert linha["cenario"] == {"algoritmo": "anel"}
