"""Testes da Máquina_de_Estados pura da Eleição em Anel.

Exercitam a lógica inteiramente em memória, sem rede nem contêiner: um pequeno
driver entrega as Mensagens de saída de um Nó ao `receber` do destino,
simulando a circulação no Anel. Isso é o que a separação "decidir vs enviar"
torna possível (Requisito 4.1).

Cobrem:
- eleição a partir de qualquer iniciador converge para o maior id (Req. 4.2–4.5);
- rodadas concorrentes convergem para o mesmo Líder (Req. 4.7);
- com um Nó marcado morto, a eleição salta e ainda converge (Req. 4.6).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.anel.maquina import (
    EstadoAnel,
    TIPO_ELEICAO,
    TIPO_LIDER,
    com_sucessor_falho,
    iniciar_eleicao,
    receber,
)
from src.comum.modelos import Mensagem


# ---------------------------------------------------------------------------
# Driver de simulação em memória
# ---------------------------------------------------------------------------


class AnelSimulado:
    """Entrega Mensagens entre Nós de um Anel chamando as funções puras.

    Mantém um estado por Nó e uma fila de Mensagens em trânsito. `mortos` é o
    conjunto de ids que não respondem: quando uma Mensagem é endereçada a um
    morto, o driver não a entrega; em vez disso, invoca `com_sucessor_falho` no
    remetente, modelando o estouro de Deadline da camada de rede (Req. 4.6).

    Devolve, ao final, o estado de cada Nó e o histórico de Mensagens trocadas,
    para que os testes verifiquem convergência e para a demo imprimir o tráfego.
    """

    def __init__(self, anel: tuple[int, ...], mortos: frozenset[int] = frozenset()) -> None:
        self.anel = anel
        self.mortos = mortos
        self.estados: dict[int, EstadoAnel] = {
            no_id: EstadoAnel(no_id=no_id, anel=anel, mortos=mortos)
            for no_id in anel
            if no_id not in mortos
        }
        self.historico: list[Mensagem] = []

    def _entregar(self, pendentes: list[Mensagem]) -> None:
        """Processa Mensagens até esvaziar a fila, aplicando a regra de salto."""
        fila = list(pendentes)
        # Guarda contra laços infinitos num bug: um Anel finito converge em um
        # número de passos proporcional ao tráfego esperado por rodada.
        limite = 1000 * max(1, len(self.anel))
        passos = 0
        while fila:
            passos += 1
            if passos > limite:
                raise AssertionError("circulação não convergiu: possível laço infinito")
            msg = fila.pop(0)
            self.historico.append(msg)

            if msg.destino in self.mortos:
                # Destino falho: o remetente detecta por timeout e salta.
                remetente = self.estados[msg.origem]
                novo, saida = com_sucessor_falho(remetente, msg, msg.destino)
                self.estados[msg.origem] = novo
                fila.extend(saida)
                continue

            destino = self.estados[msg.destino]
            novo, saida = receber(destino, msg)
            self.estados[msg.destino] = novo
            fila.extend(saida)

    def disparar(self, iniciador: int) -> None:
        """Dispara uma eleição a partir de `iniciador` e processa até estabilizar."""
        estado, saida = iniciar_eleicao(self.estados[iniciador])
        self.estados[iniciador] = estado
        self._entregar(saida)

    def disparar_concorrente(self, iniciadores: list[int]) -> None:
        """Dispara várias eleições e intercala as Mensagens em trânsito."""
        fila: list[Mensagem] = []
        for iniciador in iniciadores:
            estado, saida = iniciar_eleicao(self.estados[iniciador])
            self.estados[iniciador] = estado
            fila.extend(saida)
        self._entregar(fila)

    def vivos(self) -> list[int]:
        return [no_id for no_id in self.anel if no_id not in self.mortos]

    def lideres(self) -> dict[int, int | None]:
        return {no_id: self.estados[no_id].lider for no_id in self.vivos()}


ANEL5 = (1, 2, 3, 4, 5)


# ---------------------------------------------------------------------------
# Convergência a partir de qualquer iniciador (Req. 4.2–4.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iniciador", list(ANEL5))
def test_converge_para_maior_id_de_qualquer_iniciador(iniciador: int) -> None:
    sim = AnelSimulado(ANEL5)
    sim.disparar(iniciador)
    lideres = sim.lideres()
    assert set(lideres.values()) == {5}, lideres
    assert all(l == 5 for l in lideres.values())


def test_iniciador_encerra_sem_reenviar_a_mensagem_lider() -> None:
    # Req. 4.5: a Mensagem_Lider que volta ao iniciador não é repassada de novo.
    sim = AnelSimulado(ANEL5)
    sim.disparar(1)
    # A rodada do iniciador 1 consta como concluída em quem a originou.
    assert 1 in sim.estados[1].rodadas_concluidas
    # O iniciador não deve ter emitido uma Mensagem_Lider após recebê-la de volta:
    # contamos quantas Mensagem_Lider têm origem 1. Deve haver no máximo uma volta.
    lider_da_origem_1 = [
        m for m in sim.historico if m.tipo == TIPO_LIDER and m.origem == 1
    ]
    # O iniciador 1 só emite Mensagem_Lider se for ele quem fecha a volta da
    # eleição; nesse anel quem fecha é quem recebe a eleição de volta. De todo
    # modo, nunca deve reenviar após a Mensagem_Lider retornar a ele.
    destinos = [m.destino for m in lider_da_origem_1]
    assert len(destinos) == len(set(destinos)) or not destinos


def test_anel_de_um_no_e_lider_trivial() -> None:
    estado = EstadoAnel(no_id=7, anel=(7,))
    novo, saida = iniciar_eleicao(estado)
    assert saida == []
    assert novo.lider == 7


def test_mensagem_eleicao_acumula_ids_em_ordem() -> None:
    # Verifica o acúmulo do conjunto no primeiro repasse (Req. 4.3).
    estado2 = EstadoAnel(no_id=2, anel=ANEL5)
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"iniciador": 1, "ids": [1]}
    )
    _, saida = receber(estado2, msg)
    assert len(saida) == 1
    adiante = saida[0]
    assert adiante.tipo == TIPO_ELEICAO
    assert adiante.destino == 3
    assert list(adiante.corpo["ids"]) == [1, 2]
    assert adiante.corpo["iniciador"] == 1


# ---------------------------------------------------------------------------
# Rodadas concorrentes convergem para o mesmo Líder (Req. 4.7)
# ---------------------------------------------------------------------------


def test_rodadas_concorrentes_convergem_para_o_mesmo_lider() -> None:
    sim = AnelSimulado(ANEL5)
    # Dois Nós quaisquer disparam ao mesmo tempo; as Mensagens se intercalam.
    sim.disparar_concorrente([1, 3])
    lideres = sim.lideres()
    assert set(lideres.values()) == {5}, lideres


def test_todas_as_rodadas_concorrentes_possiveis_convergem() -> None:
    # Qualquer par de iniciadores concorrentes deve convergir para o maior id.
    for a in ANEL5:
        for b in ANEL5:
            if a >= b:
                continue
            sim = AnelSimulado(ANEL5)
            sim.disparar_concorrente([a, b])
            lideres = sim.lideres()
            assert set(lideres.values()) == {5}, (a, b, lideres)


# ---------------------------------------------------------------------------
# Salto de Nó morto e convergência entre os vivos (Req. 4.6)
# ---------------------------------------------------------------------------


def test_salta_no_morto_e_ainda_converge() -> None:
    # O maior id (5) está morto: a eleição deve convergir para o maior id vivo (4).
    sim = AnelSimulado(ANEL5, mortos=frozenset({5}))
    sim.disparar(1)
    lideres = sim.lideres()
    assert set(lideres.values()) == {4}, lideres
    assert 5 not in lideres


def test_salta_no_morto_intermediario() -> None:
    # Um morto no meio do Anel (3) é pulado; o Líder segue sendo o maior vivo (5).
    sim = AnelSimulado(ANEL5, mortos=frozenset({3}))
    sim.disparar(1)
    lideres = sim.lideres()
    assert set(lideres.values()) == {5}, lideres


def test_salta_varios_mortos_consecutivos() -> None:
    # 4 e 5 mortos: convergência para o maior vivo (3), saltando dois seguidos.
    sim = AnelSimulado(ANEL5, mortos=frozenset({4, 5}))
    sim.disparar(1)
    lideres = sim.lideres()
    assert set(lideres.values()) == {3}, lideres


def test_com_sucessor_falho_reemite_ao_proximo_vivo() -> None:
    # Unidade: o salto marca o morto e reemite a MESMA Mensagem ao próximo vivo.
    estado = EstadoAnel(no_id=1, anel=ANEL5)
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"iniciador": 1, "ids": [1]}
    )
    novo, saida = com_sucessor_falho(estado, msg, sucessor_falho=2)
    assert 2 in novo.mortos
    assert len(saida) == 1
    assert saida[0].destino == 3
    assert saida[0].corpo["ids"] == [1]


def test_com_sucessor_falho_sem_mais_vivos_descarta() -> None:
    # Anel degenerado a um único vivo: a Mensagem não tem para onde seguir.
    estado = EstadoAnel(no_id=1, anel=(1, 2), mortos=frozenset())
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"iniciador": 1, "ids": [1]}
    )
    novo, saida = com_sucessor_falho(estado, msg, sucessor_falho=2)
    assert 2 in novo.mortos
    assert saida == []


# ---------------------------------------------------------------------------
# Pureza e imutabilidade
# ---------------------------------------------------------------------------


def test_funcoes_nao_mutam_o_estado_recebido() -> None:
    estado = EstadoAnel(no_id=2, anel=ANEL5)
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"iniciador": 1, "ids": [1]}
    )
    antes_mortos = estado.mortos
    antes_lider = estado.lider
    receber(estado, msg)
    # O estado original permanece inalterado: a função devolve um novo valor.
    assert estado.mortos is antes_mortos
    assert estado.lider == antes_lider


def test_estado_rejeita_no_fora_do_anel() -> None:
    with pytest.raises(ValueError):
        EstadoAnel(no_id=9, anel=ANEL5)


def test_sucessor_pula_mortos() -> None:
    estado = EstadoAnel(no_id=1, anel=ANEL5, mortos=frozenset({2, 3}))
    assert estado.meu_sucessor == 4
