"""Cenários de falha de ponta a ponta e sua verificação.

Dois níveis, como no resto do projeto:

- **Sem Docker**: valida a lógica do `scripts/cenario.py` e do `scripts/demo.py`
  que NÃO precisa de contêiner — a normalização/validação de modos, a montagem
  dos overrides de cenário com a Semente, o registro da Semente, a coleta de
  cada log uma vez, o código 1 quando o desfecho difere do esperado, e a prova
  de que um log com Participantes em Estado_Em_Duvida, como o que fica enquanto
  o Coordenador está fora, produz AVISO e não REPROVA através de
  `src.analise.invariantes`. Também cobre o caminho de
  degradação sem Docker (código 2).

- **Com Docker** (marcados `slow`): sobem cada cenário de falha de ponta a ponta
  sobre o compose, coletam os logs reais e conferem que `verificar.py` passa.
  No `crash-coord`, o processo do Coordenador morre depois de decidir, os
  Participantes ficam em dúvida, e o contêiner reiniciado os tira dela. Eles
  PULAM de forma limpa sem Docker. Como subir vários stacks em paralelo pode
  estourar o `--wait`, estes testes devem rodar um de cada vez.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts import cenario, demo, verificar
from src.analise import invariantes as inv
from src.experimentos import conteineres
from tests.apoio import escrever_jsonl


# --------------------------------------------------------------------------- #
# Sem Docker: normalização e validação de modos.
# --------------------------------------------------------------------------- #


def test_normalizar_aliases_2pc() -> None:
    # normal == commit, falha == abort; crash-coord é próprio.
    assert cenario._normalizar("2pc", "normal") == ("commit", "commit")
    assert cenario._normalizar("2pc", "falha") == ("abort", "abort")
    assert cenario._normalizar("2pc", "commit") == ("commit", "commit")
    assert cenario._normalizar("2pc", "abort") == ("abort", "abort")
    assert cenario._normalizar("2pc", "crash-coord") == ("crash-coord", "crash-coord")


def test_normalizar_anel_valido_e_invalido() -> None:
    assert cenario._normalizar("anel", "normal") == ("normal", "normal")
    assert cenario._normalizar("anel", "falha") == ("falha", "falha")
    with pytest.raises(ValueError):
        cenario._normalizar("anel", "crash-coord")


def test_modo_2pc_invalido_retorna_codigo_1(tmp_path: Path, monkeypatch) -> None:
    # Validação antes de qualquer efeito: nem chega a checar Docker.
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "2pc", "inexistente", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


def test_semente_negativa_retorna_codigo_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "anel", "normal", tmp_path, semente=-1, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


# --------------------------------------------------------------------------- #
# Sem Docker: overrides de cenário carregam a Semente e o modo certos.
# --------------------------------------------------------------------------- #


def test_override_2pc_commit_vota_sim() -> None:
    txt = cenario.override_2pc("commit", 7)
    assert 'DOISPC_VOTO: "SIM"' in txt
    assert 'DOISPC_CAIR_APOS_DECIDIR: "false"' in txt
    assert txt.count('SEMENTE: "7"') == 4  # coord + 3 participantes


def test_override_2pc_abort_participante3_vota_nao() -> None:
    txt = cenario.override_2pc("abort", 0)
    # Apenas o Participante 3 vota NAO.
    assert 'DOISPC_VOTO: "NAO"' in txt
    assert txt.count('DOISPC_VOTO: "SIM"') == 2


def test_override_2pc_crash_coord_derruba_o_coordenador() -> None:
    txt = cenario.override_2pc("crash-coord", 3)
    assert 'DOISPC_CAIR_APOS_DECIDIR: "true"' in txt
    # Todos votam SIM no cenário de crash (para ficarem em dúvida).
    assert txt.count('DOISPC_VOTO: "SIM"') == 3


def test_override_anel_fixa_semente_nos_cinco_nos() -> None:
    txt = cenario._override_anel(42)
    assert txt.count('SEMENTE: "42"') == 5
    for i in range(1, 6):
        assert f"no-anel-{i}:" in txt


# --------------------------------------------------------------------------- #
# Sem Docker: Participante em dúvida vira AVISO, não REPROVA.
# --------------------------------------------------------------------------- #


def _registro(base: Path, algoritmo: str, rotulo: str) -> dict:
    return json.loads(
        (base / f"cenario_{algoritmo}_{rotulo}.json").read_text(encoding="utf-8")
    )


def test_participante_em_duvida_vira_aviso_nao_reprova(tmp_path: Path) -> None:
    """Imita os logs coletados com o Coordenador fora depois de decidir: Voto SIM sem Decisao.

    O Coordenador gravou a Decisao (COMMIT), mas os Participantes só têm Voto SIM
    durável, porque a DECISAO não chegou. A verificação deve sair 0, com aviso e
    sem reprovação.
    """
    logs = tmp_path / "cenario_2pc_crash-coord_logs"
    # Log_Duravel do Coordenador: decidiu COMMIT.
    escrever_jsonl(
        logs / "coordenador" / "dados" / "coordenador.log",
        [{"transacao": "tx1", "fase": "decisao", "valor": "COMMIT", "no": 1}],
    )
    # Logs_Duraveis dos Participantes: votaram SIM, sem Decisao (em dúvida).
    for pid in (11, 12, 13):
        escrever_jsonl(
            logs / f"participante-{pid - 10}" / "dados" / f"participante-{pid}.log",
            [{"transacao": "tx1", "fase": "voto", "valor": "SIM", "no": pid}],
        )

    saida: list[str] = []
    codigo = verificar.verificar([logs], imprimir=saida.append)
    assert codigo == 0
    texto = "\n".join(saida)
    assert "aviso" in texto.lower()
    assert "Estado_Em_Duvida" in texto


def test_atomicidade_nao_reprova_quando_so_o_coordenador_decidiu() -> None:
    """A atomicidade não reprova com o Coordenador fora depois de decidir: só ele tem Decisao.

    Os Participantes ficam sem nenhuma Decisao durável. Logo não há COMMIT e ABORT
    coexistindo, o invariante 4 passa, e o único achado é o aviso de dúvida do
    invariante 5.
    """
    duraveis = [
        inv.EntradaDuravel("tx1", "decisao", "COMMIT", 1),  # só o Coordenador
        inv.EntradaDuravel("tx1", "voto", "SIM", 11),
        inv.EntradaDuravel("tx1", "voto", "SIM", 12),
        inv.EntradaDuravel("tx1", "voto", "SIM", 13),
    ]
    d = inv.Dados(duraveis=tuple(duraveis))
    v4 = inv.invariante_04_atomicidade(d)
    v5 = inv.invariante_05_durabilidade(d)
    assert v4.ok  # atomicidade intacta
    assert not v5.falhas  # não reprova
    assert v5.avisos  # mas avisa do Estado_Em_Duvida


# --------------------------------------------------------------------------- #
# Sem Docker: coleta dos logs e conferência do desfecho.
# --------------------------------------------------------------------------- #


def test_coleta_do_2pc_copia_cada_volume_uma_vez(tmp_path: Path, monkeypatch) -> None:
    copias: list[tuple[str, str, Path]] = []
    monkeypatch.setattr(
        conteineres,
        "copiar_de_container",
        lambda servico, origem, destino, _arquivos: copias.append((servico, origem, destino)),
    )
    conteineres.coletar_logs("2pc", conteineres.SERVICOS_2PC, tmp_path, [])
    assert copias == [(s, "/dados/.", tmp_path / s) for s in conteineres.SERVICOS_2PC]


@pytest.mark.parametrize(("decisao", "codigo"), [("COMMIT", 0), ("ABORT", 1)])
def test_cenario_sai_1_quando_o_desfecho_difere_do_esperado(
    tmp_path: Path, monkeypatch, decisao: str, codigo: int
) -> None:
    # Um `2pc commit` que termina em ABORT passa nos invariantes de safety, e só
    # a conferência do desfecho o pega. O compose e a condução são substituídos.
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    monkeypatch.setattr(
        cenario, "compose", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    )
    monkeypatch.setattr(cenario, "coletar_logs", lambda *a: None)
    monkeypatch.setattr(cenario, "_coletar_stdout", lambda *a: None)
    monkeypatch.setattr(
        cenario,
        "_cenario_2pc",
        lambda *a: {"cenario": "2pc_commit", "decisao": decisao, "esperado": {"decisao": "COMMIT"}},
    )
    erro = io.StringIO()
    assert cenario.executar_cenario("2pc", "commit", tmp_path, saida=io.StringIO(), erro=erro) == codigo
    reg = _registro(tmp_path, "2pc", "commit")
    assert reg["desfecho"]["ok"] is (codigo == 0)
    assert ("difere do esperado" in erro.getvalue()) is (codigo == 1)


# --------------------------------------------------------------------------- #
# Sem Docker: caminho de degradação (código 2).
# --------------------------------------------------------------------------- #


def test_cenario_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: False)
    codigo = cenario.executar_cenario(
        "2pc", "crash-coord", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 2


def test_demo_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: False)
    codigo = demo.executar_demo(tmp_path, saida=io.StringIO(), erro=io.StringIO())
    assert codigo == 2


def test_demo_cenarios_de_falha_sao_os_dois_exigidos() -> None:
    # A demo encadeia exatamente os dois cenários de falha do enunciado.
    assert demo.CENARIOS_FALHA == (("anel", "falha"), ("2pc", "crash-coord"))


# --------------------------------------------------------------------------- #
# Com Docker (slow): cada cenário roda de ponta a ponta e verifica.
# --------------------------------------------------------------------------- #

pularsem_docker = pytest.mark.skipif(
    not conteineres.docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; cenário por contêiner pulado",
)


@pytest.mark.slow
@pularsem_docker
def test_anel_falha_e2e_verifica(tmp_path: Path) -> None:
    """`anel falha`: morte do Líder → reeleição para o maior vivo; invariantes passam."""
    codigo = cenario.executar_cenario("anel", "falha", tmp_path, semente=0)
    assert codigo == 0, "verificação deveria passar no cenário anel/falha"
    reg = _registro(tmp_path, "anel", "falha")
    assert reg["semente"] == 0
    assert reg["desfecho"]["ok"] is True
    assert reg["lideres_pos_falha"] == {str(i): 4 for i in (1, 2, 3, 4)}
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_commit_e2e_verifica(tmp_path: Path) -> None:
    """`2pc commit`: todos SIM → COMMIT em todos; atomicidade e durabilidade passam."""
    codigo = cenario.executar_cenario("2pc", "commit", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "commit")
    assert reg["semente"] == 0
    assert reg["decisao"] == "COMMIT"
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_abort_e2e_verifica(tmp_path: Path) -> None:
    """`2pc abort`: um NAO → ABORT atômico em todos; invariantes passam."""
    codigo = cenario.executar_cenario("2pc", "abort", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "abort")
    assert reg["decisao"] == "ABORT"
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_crash_coord_e2e_recupera(tmp_path: Path) -> None:
    """`2pc crash-coord`: dúvida com o Coordenador fora, COMMIT em todos depois do reinício."""
    codigo = cenario.executar_cenario("2pc", "crash-coord", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "crash-coord")
    assert reg["semente"] == 0
    assert reg["participantes_em_duvida"] == [11, 12, 13]
    assert reg["decisao"] == "COMMIT"
    assert reg["concluido"] is True
    assert set(reg["decisoes_participantes"].values()) == {"COMMIT"}
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # Ninguém termina em dúvida, então o invariante 5 não avisa nada.
    assert not any("aviso:" in l for l in reg["verificacao"]["relatorio"])
