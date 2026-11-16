"""Cenários do Bully: modos, condução, `condutor.jsonl`, pilha própria e ponta a ponta.

Sem Docker, os testes conferem os passos de cada modo e que o `condutor.jsonl` é a fonte de quem
está vivo para o invariante 09. A comparação dos arquivos de compose precisa só do CLI. Os dois
modos ponta a ponta sobem a pilha e ficam marcados `slow`.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts import cenario
from src.experimentos import conteineres
from tests.apoio import compose_cli_disponivel, escrever_jsonl, recursos_do_compose

TODOS = [1, 2, 3, 4, 5]


def test_os_tres_modos_do_bully_exigem_o_08_e_o_09() -> None:
    assert cenario.MODOS["bully"] == dict.fromkeys(("normal", "falha", "retorno"), (8, 9))


# --------------------------------------------------------------------------- #
# Sem Docker: os passos de cada modo.
# --------------------------------------------------------------------------- #


def _conduzir(monkeypatch, modo: str) -> tuple[list[str], dict]:
    """Conduz `_cenario_bully` com o compose e a sondagem trocados e devolve os passos e o registro."""
    passos: list[str] = []

    def esperar(_bases, ids, esperado, _timeout):
        passos.append(f"espera {list(ids)} no {esperado}")
        return dict.fromkeys(ids, esperado)

    def eleger(bases, ids, esperado, *, esperar):
        passos.append("eleição no 1")
        return esperar(bases, ids, esperado, 30.0), 0.5

    monkeypatch.setattr(cenario, "esperar_bully", esperar)
    monkeypatch.setattr(cenario, "eleger", eleger)
    monkeypatch.setattr(cenario, "derrubar_servico", lambda s, _a: passos.append(f"kill {s}"))
    monkeypatch.setattr(
        cenario, "reiniciar_servico", lambda s, _a: passos.append(f"start {s}") or 0.0
    )
    monkeypatch.setattr(cenario, "_exigir_saude", lambda s, *_a: passos.append(f"saúde {s}"))
    return passos, cenario._cenario_bully(modo, [], io.StringIO())


SUBIDA = [f"espera {TODOS} no 5"]
FALHA = [*SUBIDA, "kill no-bully-5", "eleição no 1", "espera [1, 2, 3, 4] no 4"]


@pytest.mark.parametrize(
    ("modo", "esperados", "acoes"),
    [
        ("normal", [*SUBIDA, "eleição no 1", f"espera {TODOS} no 5"], []),
        ("falha", FALHA, ["kill"]),
        (
            "retorno",
            [*FALHA, "start no-bully-5", "saúde no-bully-5", f"espera {TODOS} no 5"],
            ["kill", "start"],
        ),
    ],
)
def test_cada_modo_segue_os_passos_e_registra_o_condutor(
    monkeypatch, modo: str, esperados: list[str], acoes: list[str]
) -> None:
    passos, registro = _conduzir(monkeypatch, modo)
    assert passos == esperados
    assert registro["eventos_condutor"] == [{"acao": acao, "alvo": 5} for acao in acoes]
    assert cenario._divergencias(registro) == []


# --------------------------------------------------------------------------- #
# Sem Docker: o condutor.jsonl decide quem está vivo para o 09.
# --------------------------------------------------------------------------- #


def _logs_da_falha(_algoritmo, servicos, destino: Path, _arquivos) -> None:
    """Logs de uma falha do 5: os cinco aceitam o 5 na subida, e os quatro vivos o 4 depois."""
    for i, servico in enumerate(servicos, start=1):
        lideres = [5] if i == 5 else [5, 4]
        escrever_jsonl(
            destino / servico / f"bully-no-{i}.jsonl",
            [{"ts": 0.0, "no": i, "tipo": "bully_lider", "lider": lider} for lider in lideres],
        )


@pytest.mark.parametrize(("eventos", "codigo"), [([{"acao": "kill", "alvo": 5}], 0), ([], 1)])
def test_o_09_le_os_vivos_do_condutor_jsonl(
    tmp_path: Path, monkeypatch, eventos: list[dict], codigo: int
) -> None:
    # Sem o kill no condutor.jsonl, o 5 conta como vivo, e os outros terminam no 4.
    arquivos_usados: list[list[Path]] = []

    def compose(*args, arquivos=None, **_kw):
        arquivos_usados.append(arquivos)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    monkeypatch.setattr(cenario, "compose", compose)
    monkeypatch.setattr(cenario, "coletar_logs", _logs_da_falha)
    monkeypatch.setattr(cenario, "_coletar_stdout", lambda *a: None)
    monkeypatch.setattr(
        cenario, "_cenario_bully", lambda *a: {"eventos_condutor": eventos, "esperado": {}}
    )
    saida = io.StringIO()
    obtido = cenario.executar_cenario("bully", "falha", tmp_path, saida=saida, erro=io.StringIO())
    assert obtido == codigo, saida.getvalue()
    assert {arquivos[0] for arquivos in arquivos_usados} == {conteineres.COMPOSE_BULLY}
    linhas = (tmp_path / "cenario_bully_falha_logs" / "condutor.jsonl").read_text(encoding="utf-8")
    gravados = [json.loads(linha) for linha in linhas.splitlines()]
    assert [{k: g[k] for k in ("no", "tipo", "acao", "alvo")} for g in gravados] == [
        {"no": 0, "tipo": "condutor", **evento} for evento in eventos
    ]


# --------------------------------------------------------------------------- #
# Com o CLI do Docker: a pilha do Bully não colide com as outras.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not compose_cli_disponivel(), reason="Docker Compose CLI indisponível")
def test_pilha_do_bully_nao_colide_com_as_outras() -> None:
    bully = recursos_do_compose(conteineres.COMPOSE_BULLY)
    assert bully["projeto"] == {"mc714-t2-bully"}
    assert bully["portas"] == {str(porta) for porta in conteineres.PORTAS_BULLY.values()}
    for outra in (conteineres.COMPOSE, conteineres.COMPOSE_INTEGRACAO):
        recursos = recursos_do_compose(outra)
        for recurso, nomes in bully.items():
            assert not nomes & recursos[recurso], f"{recurso} em comum com {outra.name}"


# --------------------------------------------------------------------------- #
# Com Docker (slow): normal e retorno ponta a ponta.
# --------------------------------------------------------------------------- #

pularsem_docker = pytest.mark.skipif(
    not conteineres.docker_disponivel(),
    reason="Docker (daemon e Compose v2) indisponível, cenário por contêiner pulado",
)


def _registro(base: Path, modo: str) -> dict:
    return json.loads((base / f"cenario_bully_{modo}.json").read_text(encoding="utf-8"))


@pytest.mark.slow
@pularsem_docker
def test_bully_normal_e2e(tmp_path: Path) -> None:
    """Os cinco elegem o 5 ao subir, e a eleição pedida ao 1 termina no 5."""
    assert cenario.executar_cenario("bully", "normal", tmp_path, semente=0) == 0
    reg = _registro(tmp_path, "normal")
    assert reg["lideres_apos_subida"] == dict.fromkeys(map(str, TODOS), 5)
    assert reg["lideres_iniciais"] == dict.fromkeys(map(str, TODOS), 5)
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_bully_retorno_e2e(tmp_path: Path) -> None:
    """Sem o 5, os vivos elegem o 4. O 5 religado assume sem que ninguém peça eleição."""
    assert cenario.executar_cenario("bully", "retorno", tmp_path, semente=0) == 0
    reg = _registro(tmp_path, "retorno")
    assert reg["lideres_pos_falha"] == dict.fromkeys(("1", "2", "3", "4"), 4)
    assert reg["lideres_pos_retorno"] == dict.fromkeys(map(str, TODOS), 5)
    assert reg["eventos_condutor"] == [{"acao": "kill", "alvo": 5}, {"acao": "start", "alvo": 5}]
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # O 09 confere os cinco, porque o condutor.jsonl religa o 5.
    relatorio = reg["verificacao"]["relatorio"]
    assert any("9. " in linha and "os 5 Nó(s) vivo(s)" in linha for linha in relatorio)
