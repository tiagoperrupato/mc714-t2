"""Cenários de falha de ponta a ponta e sua verificação (tarefa 12).

Dois níveis, como no resto do projeto:

- **Sem Docker**: valida a lógica do `scripts/cenario.py` e do `scripts/demo.py`
  que NÃO precisa de contêiner — a normalização/validação de modos, a montagem
  dos overrides de cenário com a Semente, o registro da Semente, e a prova de
  que um Log coletado de um cenário `crash-coord` (Participantes em
  Estado_Em_Duvida) produz AVISO e não REPROVA através de `src.analise.invariantes`
  (Requisitos 8.5, 12.4). Também cobre o caminho de degradação sem Docker
  (código 2).

- **Com Docker** (marcados `slow`): sobem cada cenário de falha de ponta a ponta
  sobre o compose, coletam os logs reais e conferem que `verificar.py` passa
  (código 0, avisos permitidos no crash do Coordenador). Seguem o padrão de
  `tests/anel/test_compose.py`: PULAM de forma limpa sem Docker. Como subir
  vários stacks em paralelo pode estourar o `--wait`, estes testes devem rodar um
  de cada vez.

A ponte entre os dois níveis: o cenário `crash-coord` sintetizado aqui imita
exatamente as linhas que os Nós reais deixam (Voto SIM durável sem Decisao
durável), de modo que a asserção de "aviso, não reprova" valha com ou sem Docker.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import cenario, demo, verificar
from src.analise import invariantes as inv

_RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = _RAIZ / "compose.yaml"


# --------------------------------------------------------------------------- #
# Disponibilidade de Docker (mesmo probe dos demais testes de compose).
# --------------------------------------------------------------------------- #


def _docker_disponivel() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


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
    monkeypatch.setattr(cenario, "_docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "2pc", "inexistente", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


def test_semente_negativa_retorna_codigo_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "_docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "anel", "normal", tmp_path, semente=-1, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


# --------------------------------------------------------------------------- #
# Sem Docker: overrides de cenário carregam a Semente e o modo certos.
# --------------------------------------------------------------------------- #


def test_override_2pc_commit_vota_sim() -> None:
    txt = cenario._override_2pc("commit", 7)
    assert 'DOISPC_VOTO: "SIM"' in txt
    assert 'DOISPC_SUPRIMIR_DECISAO: "false"' in txt
    assert txt.count('SEMENTE: "7"') == 4  # coord + 3 participantes


def test_override_2pc_abort_participante3_vota_nao() -> None:
    txt = cenario._override_2pc("abort", 0)
    # Apenas o Participante 3 vota NAO.
    assert 'DOISPC_VOTO: "NAO"' in txt
    assert txt.count('DOISPC_VOTO: "SIM"') == 2


def test_override_2pc_crash_coord_suprime_decisao() -> None:
    txt = cenario._override_2pc("crash-coord", 3)
    assert 'DOISPC_SUPRIMIR_DECISAO: "true"' in txt
    # Todos votam SIM no cenário de crash (para ficarem em dúvida).
    assert txt.count('DOISPC_VOTO: "SIM"') == 3


def test_override_anel_fixa_semente_nos_cinco_nos() -> None:
    txt = cenario._override_anel(42)
    assert txt.count('SEMENTE: "42"') == 5
    for i in range(1, 6):
        assert f"no-anel-{i}:" in txt


# --------------------------------------------------------------------------- #
# Ponte sem Docker: um log de crash-coord vira AVISO, não REPROVA.
# --------------------------------------------------------------------------- #


def _escrever_jsonl(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as fluxo:
        for r in registros:
            fluxo.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_crash_coord_log_sintetico_vira_aviso_nao_reprova(tmp_path: Path) -> None:
    """Imita os logs que o cenário `2pc crash-coord` deixa: Voto SIM sem Decisao.

    O Coordenador gravou a Decisao (COMMIT), mas os Participantes só têm Voto SIM
    durável — ficaram em Estado_Em_Duvida porque a DECISAO foi suprimida. A
    verificação deve sair 0 (aviso, não reprovação), cumprindo o Requisito 12.4.
    """
    logs = tmp_path / "cenario_2pc_crash-coord_logs"
    # Log_Duravel do Coordenador: decidiu COMMIT.
    _escrever_jsonl(
        logs / "coordenador" / "dados" / "coordenador.log",
        [{"transacao": "tx1", "fase": "decisao", "valor": "COMMIT", "no": 1}],
    )
    # Logs_Duraveis dos Participantes: votaram SIM, sem Decisao (em dúvida).
    for pid in (11, 12, 13):
        _escrever_jsonl(
            logs / f"participante-{pid - 10}" / "dados" / f"participante-{pid}.log",
            [{"transacao": "tx1", "fase": "voto", "valor": "SIM", "no": pid}],
        )

    saida: list[str] = []
    codigo = verificar.verificar([logs], imprimir=saida.append)
    assert codigo == 0
    texto = "\n".join(saida)
    assert "aviso" in texto.lower()
    assert "Estado_Em_Duvida" in texto


def test_crash_coord_atomicidade_nao_reprova_quando_coordenador_so_decidiu(tmp_path: Path) -> None:
    """A atomicidade não reprova no crash do Coordenador: só ele tem Decisao.

    No crash-coord, apenas o Coordenador grava a Decisao; os Participantes ficam
    sem nenhuma Decisao durável. Logo não há COMMIT e ABORT coexistindo — o
    invariante 4 passa, e o único achado é o aviso de dúvida do invariante 5.
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
    assert v5.ok  # não reprova
    assert v5.avisos  # mas avisa do Estado_Em_Duvida


# --------------------------------------------------------------------------- #
# Sem Docker: caminho de degradação (código 2).
# --------------------------------------------------------------------------- #


def test_cenario_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "_docker_disponivel", lambda: False)
    codigo = cenario.executar_cenario(
        "2pc", "crash-coord", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 2


def test_demo_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "_docker_disponivel", lambda: False)
    codigo = demo.executar_demo(tmp_path, saida=io.StringIO(), erro=io.StringIO())
    assert codigo == 2


def test_demo_cenarios_de_falha_sao_os_dois_exigidos() -> None:
    # A demo encadeia exatamente os dois cenários de falha do enunciado.
    assert demo.CENARIOS_FALHA == (("anel", "falha"), ("2pc", "crash-coord"))


# --------------------------------------------------------------------------- #
# Com Docker (slow): cada cenário roda de ponta a ponta e verifica.
# --------------------------------------------------------------------------- #

pularsem_docker = pytest.mark.skipif(
    not _docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; cenário por contêiner pulado",
)


def _registro(base: Path, algoritmo: str, rotulo: str) -> dict:
    return json.loads(
        (base / f"cenario_{algoritmo}_{rotulo}.json").read_text(encoding="utf-8")
    )


@pytest.mark.slow
@pularsem_docker
def test_anel_falha_e2e_verifica(tmp_path: Path) -> None:
    """`anel falha`: morte do Líder → reeleição para o maior vivo; invariantes passam."""
    codigo = cenario.executar_cenario("anel", "falha", tmp_path, semente=0)
    assert codigo == 0, "verificação deveria passar no cenário anel/falha"
    reg = _registro(tmp_path, "anel", "falha")
    assert reg["semente"] == 0
    assert reg["convergiu_inicial"] is True
    assert reg["reeleicao_ok"] is True
    assert reg["lider_pos_falha"] == 4
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
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_abort_e2e_verifica(tmp_path: Path) -> None:
    """`2pc abort`: um NAO → ABORT atômico em todos; invariantes passam."""
    codigo = cenario.executar_cenario("2pc", "abort", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "abort")
    assert reg["decisao"] == "ABORT"
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_crash_coord_e2e_verifica_com_aviso(tmp_path: Path) -> None:
    """`2pc crash-coord`: Participantes em Estado_Em_Duvida; verificação sai 0 (aviso)."""
    codigo = cenario.executar_cenario("2pc", "crash-coord", tmp_path, semente=0)
    assert codigo == 0, "crash-coord deve passar com aviso, não reprovar (Req. 12.4)"
    reg = _registro(tmp_path, "2pc", "crash-coord")
    assert reg["semente"] == 0
    assert reg["verificacao"]["ok"] is True
    # O relatório de verificação registra o aviso de Estado_Em_Duvida.
    assert any("aviso" in l.lower() for l in reg["verificacao"]["relatorio"])
    assert reg["participantes_em_duvida"] == [11, 12, 13]
