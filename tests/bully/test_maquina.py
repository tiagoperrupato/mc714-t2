"""Testes da Máquina_de_Estados pura do Bully, em memória.

Há um teste por regra e uma propriedade sob escalonamento sorteado por Semente: com perda,
duplicata, quedas e retornos, todo vivo termina no maior id vivo e nenhum Nó aceita Líder menor
que ele.
"""

from __future__ import annotations

import ast
import random
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from src.bully import maquina
from src.bully.maquina import (
    FASE_AGUARDANDO_COORDENADOR,
    FASE_AGUARDANDO_OK,
    FASE_OCIOSA,
    PRAZO_COORDENADOR,
    PRAZO_OK,
    TIPO_COORDENADOR,
    TIPO_ELEICAO,
    TIPO_OK,
    Acao,
    EstadoBully,
    Prazo,
    iniciar_eleicao,
    prazo_expirou,
    receber,
)
from src.comum.modelos import Mensagem

IDS = (1, 2, 3, 4, 5)
TODAS_AS_FASES = (FASE_OCIOSA, FASE_AGUARDANDO_OK, FASE_AGUARDANDO_COORDENADOR)


def _msg(tipo: str, origem: int, destino: int, **corpo: object) -> Mensagem:
    return Mensagem(tipo=tipo, origem=origem, destino=destino, corpo=corpo)


def _coordenador_a_todos(lider: int) -> list[Acao]:
    return [_msg(TIPO_COORDENADOR, lider, d, lider=lider) for d in IDS if d != lider]


# ---------------------------------------------------------------------------
# Convocação
# ---------------------------------------------------------------------------


def test_convocar_sem_maiores_assume_e_anuncia_a_todos() -> None:
    novo, acoes = iniciar_eleicao(EstadoBully(no_id=5, ids=IDS))
    assert (novo.lider, novo.fase, novo.epoca) == (5, FASE_OCIOSA, 1)
    assert acoes == _coordenador_a_todos(5)


def test_convocar_com_maiores_desafia_cada_um_e_pede_prazo_de_ok() -> None:
    novo, acoes = iniciar_eleicao(EstadoBully(no_id=3, ids=IDS, lider=5, epoca=2))
    assert (novo.lider, novo.fase, novo.epoca) == (5, FASE_AGUARDANDO_OK, 3)
    assert acoes == [
        _msg(TIPO_ELEICAO, 3, 4, epoca=3),
        _msg(TIPO_ELEICAO, 3, 5, epoca=3),
        Prazo(PRAZO_OK, 3),
    ]


@pytest.mark.parametrize("fase", [FASE_AGUARDANDO_OK, FASE_AGUARDANDO_COORDENADOR])
def test_convocar_fora_de_ociosa_nao_faz_nada(fase: str) -> None:
    estado = EstadoBully(no_id=3, ids=IDS, fase=fase, epoca=2)
    assert iniciar_eleicao(estado) == (estado, [])


# ---------------------------------------------------------------------------
# ELEICAO
# ---------------------------------------------------------------------------


def test_lider_em_exercicio_responde_so_ok_com_o_lider() -> None:
    estado = EstadoBully(no_id=5, ids=IDS, lider=5, epoca=1)
    novo, acoes = receber(estado, _msg(TIPO_ELEICAO, 2, 5, epoca=7))
    assert novo == estado
    assert acoes == [_msg(TIPO_OK, 5, 2, epoca=7, lider=5)]


def test_ocioso_que_nao_e_lider_responde_ok_e_depois_convoca() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, lider=5)
    novo, acoes = receber(estado, _msg(TIPO_ELEICAO, 1, 3, epoca=4))
    assert (novo.fase, novo.epoca) == (FASE_AGUARDANDO_OK, 1)
    assert acoes == [
        _msg(TIPO_OK, 3, 1, epoca=4, lider=None),
        _msg(TIPO_ELEICAO, 3, 4, epoca=1),
        _msg(TIPO_ELEICAO, 3, 5, epoca=1),
        Prazo(PRAZO_OK, 1),
    ]


def test_eleicao_durante_outra_eleicao_so_recebe_ok_sem_lider() -> None:
    # Quem se tem por Líder mas reabriu eleição não está em exercício.
    estado = EstadoBully(no_id=3, ids=IDS, lider=3, fase=FASE_AGUARDANDO_OK, epoca=2)
    assert receber(estado, _msg(TIPO_ELEICAO, 2, 3, epoca=6)) == (
        estado,
        [_msg(TIPO_OK, 3, 2, epoca=6, lider=None)],
    )


def test_eleicao_de_id_maior_e_ignorada() -> None:
    estado = EstadoBully(no_id=3, ids=IDS)
    assert receber(estado, _msg(TIPO_ELEICAO, 4, 3, epoca=1)) == (estado, [])


# ---------------------------------------------------------------------------
# OK
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fase", TODAS_AS_FASES)
def test_ok_com_lider_maior_e_aceito_em_qualquer_fase(fase: str) -> None:
    estado = EstadoBully(no_id=2, ids=IDS, fase=fase, epoca=3)
    novo, acoes = receber(estado, _msg(TIPO_OK, 5, 2, epoca=1, lider=5))
    assert (novo.lider, novo.fase, novo.epoca, acoes) == (5, FASE_OCIOSA, 3, [])


def test_ok_atrasado_no_ocioso_com_lider_maior_e_ignorado() -> None:
    estado = EstadoBully(no_id=1, ids=IDS, lider=5, epoca=1)
    assert receber(estado, _msg(TIPO_OK, 4, 1, epoca=1, lider=4)) == (estado, [])


def test_ok_atrasado_nao_desfaz_o_coordenador_do_maior_que_voltou() -> None:
    """O 4 lidera com o 5 fora, o 1 convoca, o 5 volta e assume antes de o OK(4) chegar ao 1."""
    um, eleicoes = iniciar_eleicao(EstadoBully(no_id=1, ids=IDS, lider=4))
    eleicao_ao_4 = next(m for m in eleicoes if isinstance(m, Mensagem) and m.destino == 4)
    _, [ok_do_4] = receber(EstadoBully(no_id=4, ids=IDS, lider=4), eleicao_ao_4)
    _, anuncios = iniciar_eleicao(EstadoBully(no_id=5, ids=IDS))
    coordenador_ao_1 = next(m for m in anuncios if m.destino == 1)
    um, _ = receber(um, coordenador_ao_1)
    um, _ = receber(um, ok_do_4)
    assert (um.lider, um.fase) == (5, FASE_OCIOSA)


def test_ok_sem_lider_da_epoca_atual_passa_a_esperar_o_coordenador() -> None:
    estado = EstadoBully(no_id=2, ids=IDS, fase=FASE_AGUARDANDO_OK, epoca=3)
    novo, acoes = receber(estado, _msg(TIPO_OK, 4, 2, epoca=3, lider=None))
    assert novo == replace(estado, fase=FASE_AGUARDANDO_COORDENADOR)
    assert acoes == [Prazo(PRAZO_COORDENADOR, 3)]


@pytest.mark.parametrize(
    ("fase", "epoca_do_ok"),
    [
        (FASE_AGUARDANDO_OK, 2),
        (FASE_AGUARDANDO_COORDENADOR, 3),
        (FASE_OCIOSA, 3),
    ],
    ids=["epoca_velha", "ja_aguarda_coordenador", "ociosa"],
)
def test_outro_ok_sem_lider_e_ignorado(fase: str, epoca_do_ok: int) -> None:
    estado = EstadoBully(no_id=2, ids=IDS, fase=fase, epoca=3)
    assert receber(estado, _msg(TIPO_OK, 4, 2, epoca=epoca_do_ok, lider=None)) == (estado, [])


# ---------------------------------------------------------------------------
# COORDENADOR
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fase", TODAS_AS_FASES)
def test_coordenador_maior_e_aceito_em_qualquer_fase(fase: str) -> None:
    estado = EstadoBully(no_id=3, ids=IDS, lider=3, fase=fase, epoca=2)
    novo, acoes = receber(estado, _msg(TIPO_COORDENADOR, 4, 3, lider=4))
    assert (novo.lider, novo.fase, novo.epoca, acoes) == (4, FASE_OCIOSA, 2, [])


def test_coordenador_menor_e_rejeitado_e_o_maior_retoma_a_lideranca() -> None:
    estado = EstadoBully(no_id=5, ids=IDS, lider=5, epoca=1)
    novo, acoes = receber(estado, _msg(TIPO_COORDENADOR, 4, 5, lider=4))
    assert (novo.lider, novo.fase, novo.epoca) == (5, FASE_OCIOSA, 2)
    assert acoes == _coordenador_a_todos(5)


def test_coordenador_menor_e_rejeitado_e_o_no_desafia_os_maiores() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, lider=5, epoca=1)
    novo, acoes = receber(estado, _msg(TIPO_COORDENADOR, 2, 3, lider=2))
    assert (novo.lider, novo.fase, novo.epoca) == (5, FASE_AGUARDANDO_OK, 2)
    assert acoes == [
        _msg(TIPO_ELEICAO, 3, 4, epoca=2),
        _msg(TIPO_ELEICAO, 3, 5, epoca=2),
        Prazo(PRAZO_OK, 2),
    ]


def test_coordenador_menor_durante_uma_eleicao_so_e_rejeitado() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, fase=FASE_AGUARDANDO_COORDENADOR, epoca=2)
    assert receber(estado, _msg(TIPO_COORDENADOR, 2, 3, lider=2)) == (estado, [])


def test_tipo_desconhecido_e_ignorado() -> None:
    estado = EstadoBully(no_id=3, ids=IDS)
    assert receber(estado, _msg("desconhecido", 1, 3)) == (estado, [])


# ---------------------------------------------------------------------------
# Prazos
# ---------------------------------------------------------------------------


def test_prazo_de_ok_sem_resposta_assume() -> None:
    estado = EstadoBully(no_id=4, ids=IDS, lider=5, fase=FASE_AGUARDANDO_OK, epoca=2)
    novo, acoes = prazo_expirou(estado, Prazo(PRAZO_OK, 2))
    assert (novo.lider, novo.fase, novo.epoca) == (4, FASE_OCIOSA, 2)
    assert acoes == _coordenador_a_todos(4)


def test_prazo_de_coordenador_convoca_de_novo_com_epoca_nova() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, fase=FASE_AGUARDANDO_COORDENADOR, epoca=3)
    novo, acoes = prazo_expirou(estado, Prazo(PRAZO_COORDENADOR, 3))
    assert (novo.fase, novo.epoca) == (FASE_AGUARDANDO_OK, 4)
    assert acoes == [
        _msg(TIPO_ELEICAO, 3, 4, epoca=4),
        _msg(TIPO_ELEICAO, 3, 5, epoca=4),
        Prazo(PRAZO_OK, 4),
    ]


def test_prazo_velho_e_ignorado_depois_de_uma_eleicao_nova() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, fase=FASE_AGUARDANDO_OK, epoca=4)
    assert prazo_expirou(estado, Prazo(PRAZO_OK, 3)) == (estado, [])


@pytest.mark.parametrize(
    ("fase", "tipo"),
    [
        (FASE_OCIOSA, PRAZO_OK),
        (FASE_OCIOSA, PRAZO_COORDENADOR),
        (FASE_AGUARDANDO_OK, PRAZO_COORDENADOR),
        (FASE_AGUARDANDO_COORDENADOR, PRAZO_OK),
    ],
)
def test_prazo_em_outra_fase_nao_faz_nada(fase: str, tipo: str) -> None:
    estado = EstadoBully(no_id=3, ids=IDS, lider=5, fase=fase, epoca=2)
    assert prazo_expirou(estado, Prazo(tipo, 2)) == (estado, [])


# ---------------------------------------------------------------------------
# Estado e pureza
# ---------------------------------------------------------------------------


def test_funcoes_nao_mutam_o_estado_recebido() -> None:
    estado = EstadoBully(no_id=3, ids=IDS, lider=5, epoca=2)
    copia = replace(estado)
    iniciar_eleicao(estado)
    receber(estado, _msg(TIPO_ELEICAO, 1, 3, epoca=1))
    receber(estado, _msg(TIPO_COORDENADOR, 4, 3, lider=4))
    prazo_expirou(estado, Prazo(PRAZO_OK, 2))
    assert estado == copia


def test_estado_recusa_id_fora_de_ids() -> None:
    with pytest.raises(ValueError, match="não pertence"):
        EstadoBully(no_id=9, ids=IDS)


def test_estado_recusa_ids_repetidos() -> None:
    with pytest.raises(ValueError, match="repetidos"):
        EstadoBully(no_id=1, ids=(1, 2, 2))


def test_estado_guarda_ids_em_ordem_crescente() -> None:
    estado = EstadoBully(no_id=2, ids=(4, 1, 2, 3))
    assert estado.ids == (1, 2, 3, 4)
    assert estado.maiores == (3, 4)


def test_maquina_nao_importa_rede_relogio_nem_sorteio() -> None:
    arvore = ast.parse(Path(maquina.__file__).read_text(encoding="utf-8"))
    modulos = set()
    for item in ast.walk(arvore):
        if isinstance(item, ast.Import):
            modulos |= {alias.name.split(".")[0] for alias in item.names}
        elif isinstance(item, ast.ImportFrom) and item.module:
            modulos.add(item.module.split(".")[0])
    assert modulos.isdisjoint({"fastapi", "httpx", "time", "random"}), modulos


# ---------------------------------------------------------------------------
# Propriedade sob escalonamento sorteado
# ---------------------------------------------------------------------------

SEMENTES = 500
MAX_PASSOS = 100_000


class BullySimulado:
    """Entrega as ações das funções puras a três a sete Nós, em ordem sorteada pela Semente.

    Mensagem perdida volta à fila, como o reenvio do remetente, e a duplicada é entregue de novo
    em qualquer ordem. Quedas, retornos, prazos e a convocação de quem perdeu o Líder só
    acontecem com a fila vazia, o modelo síncrono sob o qual o Bully é correto.
    """

    def __init__(self, semente: int) -> None:
        self.rng = random.Random(semente)
        self.ids = tuple(range(1, self.rng.randint(3, 7) + 1))
        self.perda = self.rng.choice((0.0, 0.2))
        self.duplicata = self.rng.choice((0.0, 0.2))
        self.perturbacoes = self.rng.randint(0, 4)
        self.estados = {i: EstadoBully(no_id=i, ids=self.ids) for i in self.ids}
        self.vivos = set(self.ids)
        self.fila: list[Mensagem] = []
        self.prazos: list[tuple[int, Prazo]] = []
        self.aceites_menores: list[tuple[int, int]] = []
        self.cobertura: Counter[str] = Counter()

    def executar(self) -> None:
        for i in self.rng.sample(self.ids, self.rng.randint(1, len(self.ids))):
            self._aplicar(i, iniciar_eleicao(self.estados[i]))
        for _ in range(MAX_PASSOS):
            if self.fila:
                self._entregar_uma()
            elif not self._agir_na_quiescencia():
                return
        raise AssertionError(f"não chegou à quiescência em {MAX_PASSOS} passos")

    def _aplicar(self, no_id: int, transicao: tuple[EstadoBully, list[Acao]]) -> None:
        novo, acoes = transicao
        if novo.lider != self.estados[no_id].lider and novo.lider < no_id:
            self.aceites_menores.append((no_id, novo.lider))
        self.estados[no_id] = novo
        for acao in acoes:
            if isinstance(acao, Prazo):
                self.prazos.append((no_id, acao))
            else:
                self.fila.append(acao)

    def _entregar_uma(self) -> None:
        msg = self.fila.pop(self.rng.randrange(len(self.fila)))
        if msg.destino not in self.vivos:
            return
        if self.rng.random() < self.perda:
            self.fila.append(msg)
            self.cobertura["perda"] += 1
            return
        if self.rng.random() < self.duplicata:
            self.fila.append(msg)
            self.cobertura["duplicata"] += 1
        self._aplicar(msg.destino, receber(self.estados[msg.destino], msg))

    def _agir_na_quiescencia(self) -> bool:
        """Sorteia uma ação possível com a fila vazia, ou devolve False se não resta nenhuma."""
        orfaos = [
            i
            for i in sorted(self.vivos)
            if self.estados[i].fase == FASE_OCIOSA and self.estados[i].lider not in self.vivos
        ]
        opcoes = []
        if self.prazos:
            opcoes.append(self._disparar_prazo)
        if orfaos:
            opcoes.append(lambda: self._convocar_orfao(self.rng.choice(orfaos)))
        if self.perturbacoes:
            opcoes.append(self._perturbar)
        if not opcoes:
            return False
        self.rng.choice(opcoes)()
        return True

    def _disparar_prazo(self) -> None:
        no_id, prazo = self.prazos.pop(self.rng.randrange(len(self.prazos)))
        antes = self.estados[no_id]
        self._aplicar(no_id, prazo_expirou(antes, prazo))
        if self.estados[no_id] != antes:
            self.cobertura[f"prazo_{prazo.tipo}"] += 1

    def _convocar_orfao(self, no_id: int) -> None:
        self._aplicar(no_id, iniciar_eleicao(self.estados[no_id]))
        self.cobertura["orfao_convocou"] += 1

    def _perturbar(self) -> None:
        self.perturbacoes -= 1
        mortos = sorted(set(self.ids) - self.vivos)
        if mortos and (len(self.vivos) == 1 or self.rng.random() < 0.5):
            alvo = self.rng.choice(mortos)
            self.vivos.add(alvo)
            self.estados[alvo] = EstadoBully(no_id=alvo, ids=self.ids)
            self._aplicar(alvo, iniciar_eleicao(self.estados[alvo]))
            self.cobertura["retorno"] += 1
            return
        # Metade das quedas derruba o maior vivo, que costuma ser o Líder.
        alvo = max(self.vivos) if self.rng.random() < 0.5 else self.rng.choice(sorted(self.vivos))
        self.vivos.remove(alvo)
        self.prazos = [(i, p) for i, p in self.prazos if i != alvo]
        self.cobertura["queda"] += 1


def test_todo_vivo_termina_no_maior_id_vivo_sem_aceitar_lider_menor() -> None:
    cobertura: Counter[str] = Counter()
    for semente in range(SEMENTES):
        sim = BullySimulado(semente)
        sim.executar()
        maior = max(sim.vivos)
        finais = {i: (sim.estados[i].lider, sim.estados[i].fase) for i in sorted(sim.vivos)}
        assert sim.aceites_menores == [], f"semente {semente}: aceites {sim.aceites_menores}"
        assert set(finais.values()) == {(maior, FASE_OCIOSA)}, f"semente {semente}: {finais}"
        cobertura += sim.cobertura
    # Sem cada perturbação ao menos uma vez, a propriedade passaria vazia.
    esperadas = {"perda", "duplicata", "queda", "retorno", "orfao_convocou"}
    assert esperadas | {"prazo_ok", "prazo_coordenador"} <= set(cobertura), cobertura
