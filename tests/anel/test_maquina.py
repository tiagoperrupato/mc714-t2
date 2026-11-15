"""Testes da Máquina_de_Estados pura da Eleição em Anel.

Exercitam a lógica inteiramente em memória, sem rede nem contêiner: um pequeno
driver entrega as Mensagens de saída de um Nó ao `receber` do destino,
simulando a circulação no Anel. Isso é o que a separação "decidir vs enviar"
torna possível.

Cobrem:
- eleição a partir de qualquer iniciador converge para o maior id;
- rodadas concorrentes convergem para o mesmo Líder;
- com um Nó marcado morto, a eleição salta e ainda converge;
- uma eleição atrasada não fecha de novo uma rodada concluída;
- um Nó que volta participa da eleição seguinte;
- o anúncio para mesmo quando o iniciador morre no meio da rodada.
"""

from __future__ import annotations

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
    conjunto de ids que não respondem, e o teste pode mudá-lo entre rodadas:
    quando uma Mensagem é endereçada a um morto, o driver não a entrega; em vez
    disso, invoca `com_sucessor_falho` no remetente, modelando o estouro de
    Deadline da camada de rede.

    Devolve, ao final, o estado de cada Nó e o histórico de Mensagens trocadas,
    para que os testes verifiquem convergência e para a demo imprimir o tráfego.
    """

    def __init__(self, anel: tuple[int, ...], mortos: frozenset[int] = frozenset()) -> None:
        self.anel = anel
        self.mortos = mortos
        self.estados: dict[int, EstadoAnel] = {
            no_id: EstadoAnel(no_id=no_id, anel=anel) for no_id in anel
        }
        self.historico: list[Mensagem] = []
        self._rodadas = 0

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

    def iniciar(self, iniciador: int) -> tuple[str, list[Mensagem]]:
        """Abre uma rodada com id novo em `iniciador`, sem entregar nada ainda."""
        self._rodadas += 1
        rodada = f"{iniciador}-{self._rodadas}"
        self.estados[iniciador], saida = iniciar_eleicao(self.estados[iniciador], rodada)
        return rodada, saida

    def disparar(self, iniciador: int) -> str:
        """Dispara uma eleição a partir de `iniciador`, processa até estabilizar e devolve a rodada."""
        rodada, saida = self.iniciar(iniciador)
        self._entregar(saida)
        return rodada

    def disparar_concorrente(self, iniciadores: list[int]) -> None:
        """Dispara várias eleições e intercala as Mensagens em trânsito."""
        fila: list[Mensagem] = []
        for iniciador in iniciadores:
            fila.extend(self.iniciar(iniciador)[1])
        self._entregar(fila)

    def vivos(self) -> list[int]:
        return [no_id for no_id in self.anel if no_id not in self.mortos]

    def lideres(self) -> dict[int, int | None]:
        return {no_id: self.estados[no_id].lider for no_id in self.vivos()}


ANEL5 = (1, 2, 3, 4, 5)


# ---------------------------------------------------------------------------
# Convergência a partir de qualquer iniciador
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iniciador", list(ANEL5))
def test_converge_para_maior_id_de_qualquer_iniciador(iniciador: int) -> None:
    sim = AnelSimulado(ANEL5)
    sim.disparar(iniciador)
    lideres = sim.lideres()
    assert set(lideres.values()) == {5}, lideres
    assert all(l == 5 for l in lideres.values())


def test_anuncio_da_uma_volta_e_para() -> None:
    # Cada Nó repassa a Mensagem_Lider uma vez, e quem a emitiu não a
    # repassa quando ela volta.
    sim = AnelSimulado(ANEL5)
    rodada = sim.disparar(1)
    assert all(rodada in sim.estados[i].rodadas_concluidas for i in ANEL5)
    assert len([m for m in sim.historico if m.tipo == TIPO_LIDER]) == len(ANEL5)


def test_anel_de_um_no_e_lider_trivial() -> None:
    estado = EstadoAnel(no_id=7, anel=(7,))
    novo, saida = iniciar_eleicao(estado, "7-1")
    assert saida == []
    assert novo.lider == 7


def test_mensagem_eleicao_acumula_ids_em_ordem() -> None:
    # Verifica o acúmulo do conjunto no primeiro repasse.
    estado2 = EstadoAnel(no_id=2, anel=ANEL5)
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"rodada": "1-1", "ids": [1]}
    )
    _, saida = receber(estado2, msg)
    assert len(saida) == 1
    adiante = saida[0]
    assert adiante.tipo == TIPO_ELEICAO
    assert adiante.destino == 3
    assert list(adiante.corpo["ids"]) == [1, 2]
    assert adiante.corpo["rodada"] == "1-1"


# ---------------------------------------------------------------------------
# Rodadas concorrentes convergem para o mesmo Líder
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
# Salto de Nó morto e convergência entre os vivos
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
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"rodada": "1-1", "ids": [1]}
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
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"rodada": "1-1", "ids": [1]}
    )
    novo, saida = com_sucessor_falho(estado, msg, sucessor_falho=2)
    assert 2 in novo.mortos
    assert saida == []


# ---------------------------------------------------------------------------
# Rodada única, reintegração e anúncio que termina
# ---------------------------------------------------------------------------


def test_eleicao_atrasada_nao_fecha_de_novo_a_rodada() -> None:
    # O 5 estava só pausado: o 4 o saltou e a rodada elegeu 4. Ao acordar, o 5
    # acrescenta seu id à eleição atrasada e a repassa ao 1, que já fechou a rodada.
    sim = AnelSimulado(ANEL5, mortos=frozenset({5}))
    rodada = sim.disparar(1)
    assert set(sim.lideres().values()) == {4}

    atrasada = Mensagem(
        tipo=TIPO_ELEICAO, origem=5, destino=1, corpo={"rodada": rodada, "ids": [1, 2, 3, 4, 5]}
    )
    novo, saida = receber(sim.estados[1], atrasada)
    assert saida == []
    assert novo.lider == 4


def test_no_que_volta_participa_da_eleicao_seguinte() -> None:
    sim = AnelSimulado(ANEL5, mortos=frozenset({5}))
    sim.disparar(1)
    assert 5 in sim.estados[4].mortos
    assert set(sim.lideres().values()) == {4}

    sim.mortos = frozenset()  # o 5 reinicia
    sim.disparar(1)
    assert set(sim.lideres().values()) == {5}, sim.lideres()


def test_quem_manda_mensagem_sai_de_mortos() -> None:
    estado = EstadoAnel(no_id=1, anel=ANEL5, mortos=frozenset({5}))
    anuncio = Mensagem(tipo=TIPO_LIDER, origem=5, destino=1, corpo={"rodada": "3-1", "lider": 5})
    novo, _ = receber(estado, anuncio)
    assert novo.mortos == frozenset()


def test_anuncio_para_mesmo_se_o_iniciador_morre_no_meio() -> None:
    # O 1 inicia e morre. Quem fecha a volta é o 2, e o anúncio para quando
    # volta a ele, sem dar voltas atrás do iniciador.
    sim = AnelSimulado(ANEL5)
    _, saida = sim.iniciar(1)
    sim.mortos = frozenset({1})
    sim._entregar(saida)
    assert set(sim.lideres().values()) == {5}


# ---------------------------------------------------------------------------
# Pureza e imutabilidade
# ---------------------------------------------------------------------------


def test_funcoes_nao_mutam_o_estado_recebido() -> None:
    estado = EstadoAnel(no_id=2, anel=ANEL5)
    msg = Mensagem(
        tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"rodada": "1-1", "ids": [1]}
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
