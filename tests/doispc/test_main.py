"""`main` do 2PC recusa o ambiente inválido com `SystemExit` que cita a variável, antes do log."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.doispc.__main__ import _ler_voto, main
from src.doispc.log_duravel import VOTO_NAO, VOTO_SIM

_VARIAVEIS = (
    "DOISPC_PAPEL", "SEMENTE", "LOG_DIR", "COORD_ID", "COORD_HOST", "COORD_PORTA", "PART_ID",
    "PART_PORTA", "DOISPC_PARTICIPANTES", "DOISPC_COORDENADORES", "DOISPC_DEADLINE_VOTO",
    "DOISPC_DEADLINE_DECISAO", "DOISPC_DEADLINE", "DOISPC_VOTO", "DOISPC_LOG_DURAVEL",
)


def _ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **valores: str) -> Path:
    """Troca o ambiente do 2PC por `valores` e devolve a pasta de log, que não deve ser criada."""
    for nome in _VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    monkeypatch.setenv("DOISPC_LOG_DURAVEL", str(tmp_path / "duravel.log"))
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)
    return log_dir


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [("COORD_PORTA", "oito mil"), ("SEMENTE", "x"), ("DOISPC_DEADLINE_VOTO", "3s")],
)
def test_numero_malformado_do_coordenador_sai_citando_a_variavel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variavel: str, valor: str
) -> None:
    ambiente = {"DOISPC_PAPEL": "coordenador", "COORD_ID": "1", "DOISPC_PARTICIPANTES": "11@h:8000"}
    log_dir = _ambiente(monkeypatch, tmp_path, **{**ambiente, variavel: valor})

    with pytest.raises(SystemExit) as saida:
        main()

    assert f"'{variavel}'" in str(saida.value) and repr(valor) in str(saida.value)
    assert not log_dir.exists()


def test_voto_fora_de_sim_e_nao_e_recusado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_dir = _ambiente(
        monkeypatch, tmp_path,
        DOISPC_PAPEL="participante", PART_ID="11", COORD_ID="1", DOISPC_VOTO="talvez",
    )

    with pytest.raises(SystemExit, match="'DOISPC_VOTO'") as saida:
        main()

    assert "'SIM' ou 'NAO'" in str(saida.value)
    assert not log_dir.exists()


@pytest.mark.parametrize(("valor", "voto"), [(None, VOTO_SIM), ("nao", VOTO_NAO), ("SIM", VOTO_SIM)])
def test_voto_aceito_sem_distinguir_maiusculas(
    monkeypatch: pytest.MonkeyPatch, valor: str | None, voto: str
) -> None:
    monkeypatch.delenv("DOISPC_VOTO", raising=False)
    if valor is not None:
        monkeypatch.setenv("DOISPC_VOTO", valor)
    assert _ler_voto() == voto
