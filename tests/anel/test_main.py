"""`construir_no` monta o Nó do Anel a partir das variáveis de ambiente."""

from __future__ import annotations

import json

from src.anel.__main__ import construir_no
from src.comum.config import ConfigNo


def test_construir_no_le_o_ambiente_e_grava_o_inicio_da_replica(monkeypatch, tmp_path) -> None:
    for variavel, valor in {
        "NO_ID": "2",
        "ANEL": "1,2,3",
        "URLS": "1=http://a,2=http://b,3=http://c",
        "PORTA": "8002",
        "SEMENTE": "7",
        "DEADLINE": "0.5",
        "RETRIES": "1",
        "LOG_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(variavel, valor)

    no, config, porta, controlador = construir_no()
    try:
        assert config == ConfigNo(
            id=2,
            algoritmo="anel",
            porta=8002,
            semente=7,
            deadline=0.5,
            retries=1,
            sucessores=(1, 3),
        )
        assert porta == 8002
        assert controlador.semente == 7
        assert no.estado.no_id == 2
        assert no.estado.anel == (1, 2, 3)
        linha = json.loads((tmp_path / "anel-no-2.jsonl").read_text(encoding="utf-8"))
        assert linha["tipo"] == "inicio_replica"
        assert linha["semente"] == 7
        assert linha["cenario"] == {"algoritmo": "anel", "anel": [1, 2, 3], "deadline": 0.5}
    finally:
        no.parar()
