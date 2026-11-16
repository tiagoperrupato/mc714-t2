"""Reprodutibilidade de ponta a ponta documentada no README.

Exercita, SEM Docker, a mesma sequência que o README manda rodar a partir de um
checkout fresco — bateria curta do Anel e do 2PC para um `results/` temporário,
análise sobre esse `results/` (tabelas markdown e figuras geradas) e verificação
dos invariantes de correção sobre logs coletados.
O condutor do 2PC é trocado por um fake, porque o real sobe o compose e tem o
teste `slow` dele em `test_condutor_doispc.py`.

O que se garante aqui:

- apagar `results/` e rodar a bateria recria os CSVs por algoritmo, com o esquema
  esperado, e as do Anel e do Bully são determinísticas por Semente;
- a análise lê esses CSVs e grava as figuras, sem subir contêiner nem reexecutar
  Réplica;
- `scripts/verificar.py` sai com código 0 sobre logs corretos, e também sobre um
  `results/` sem logs (nada a verificar não é reprovação).
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import analisar, bateria, verificar
from src.experimentos import condutor_doispc
from tests.apoio import ler_csv


def _log_anel_convergido(destino: Path) -> None:
    """Grava um Log_Estruturado mínimo e CORRETO de uma eleição do Anel.

    Cinco Nós vivos anunciam o mesmo Líder (o maior id, 5) na rodada "1-a"
    — exatamente o formato que `src/anel/no.py` deixa e que os invariantes de
    unicidade/convergência leem. Serve para exercitar o caminho de sucesso de
    `scripts/verificar.py` sem precisar subir o compose.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    registros = [
        {
            "ts": 0.0,
            "no": no,
            "tipo": "recebeu",
            "msg_tipo": "lider",
            "origem": 0,
            "corpo": {"rodada": "1-a", "lider": 5},
        }
        for no in (1, 2, 3, 4, 5)
    ]
    with destino.open("w", encoding="utf-8") as fluxo:
        for r in registros:
            fluxo.write(json.dumps(r, ensure_ascii=False) + "\n")


def _fakes_do_condutor(monkeypatch) -> None:
    """Condutor do 2PC que devolve linhas plausíveis sem subir contêiner."""
    monkeypatch.setattr(condutor_doispc, "preparar", lambda: {})
    monkeypatch.setattr(
        condutor_doispc,
        "executar_replica_latencia",
        lambda item, *, transacoes, aquecimento, dir_logs: [
            {"ordem": o, "aquecimento": int(o < aquecimento),
             "transacao": f"lat-{item.semente}-{o:03d}", "decisao": "COMMIT",
             "latencia_commit_s": 0.01 * (1 + o + item.k), "mensagens": 12}
            for o in range(aquecimento + transacoes)
        ],
    )
    monkeypatch.setattr(
        condutor_doispc,
        "executar_replica_recuperacao",
        lambda item, *, dir_logs: {
            "transacao": f"rec-{item.semente}", "decisao": "COMMIT", "em_duvida": 3,
            "tempo_recuperacao_s": 1.0 + 0.1 * item.k, "mensagens": 12,
        },
    )


def test_sequencia_do_readme_regenera_dados_tabelas_e_figuras(tmp_path: Path, monkeypatch) -> None:
    """Apagar results/ e seguir o README recria CSVs, tabelas e figuras, e verifica.

    Reproduz, num `results/` e num `docs/` temporários, a sequência documentada:
    bateria (anel, bully e 2pc) -> análise (tabelas + figuras) -> verificação, com
    poucas Réplicas para ser rápido.
    """
    _fakes_do_condutor(monkeypatch)

    results = tmp_path / "results"
    dir_dados = tmp_path / "docs" / "dados"
    dir_figuras = tmp_path / "docs" / "figuras"
    results.mkdir(parents=True)

    # 1) Bateria curta de cada algoritmo para o results/ temporário.
    csv_anel = results / "bateria_anel_num_nos.csv"
    csv_bully = results / "bateria_bully_num_nos.csv"
    csv_latencia = results / "bateria_2pc_latencia_rede.csv"
    csv_recuperacao = results / "bateria_2pc_recuperacao.csv"
    assert bateria.main(["--algoritmo", "anel", "--replicas", "2", "--results", str(results)]) == 0
    assert bateria.main(["--algoritmo", "bully", "--replicas", "2", "--results", str(results)]) == 0
    argv_2pc = ["--algoritmo", "2pc", "--replicas", "2", "--transacoes", "3", "--aquecimento", "1"]
    assert bateria.main([*argv_2pc, "--results", str(results)]) == 0

    # Determinismo por Semente: rodar de novo produz exatamente o mesmo CSV.
    for algoritmo, csv in (("anel", csv_anel), ("bully", csv_bully)):
        primeira = csv.read_bytes()
        argv = ["--algoritmo", algoritmo, "--replicas", "2", "--results", str(results)]
        assert bateria.main(argv) == 0
        assert csv.read_bytes() == primeira

    linhas_anel = ler_csv(csv_anel)
    assert len(linhas_anel) == 6  # 3 níveis padrão x 2 Réplicas
    assert {"num_nos", "tempo_convergencia", "mensagens", "amb_so"} <= set(linhas_anel[0])
    niveis_2pc = len(bateria.NIVEIS_2PC_LATENCIA)
    linhas_latencia = ler_csv(csv_latencia)
    assert len(linhas_latencia) == niveis_2pc * 2 * 4  # 2 Réplicas x (1 aquecimento + 3)
    assert {"latencia_rede", "aquecimento", "latencia_commit_s", "mensagens", "amb_so"} <= set(
        linhas_latencia[0]
    )
    linhas_recuperacao = ler_csv(csv_recuperacao)
    assert len(linhas_recuperacao) == niveis_2pc * 2
    assert {"tempo_recuperacao_s", "em_duvida", "amb_so"} <= set(linhas_recuperacao[0])

    # 2) Análise: lê os CSVs e grava as quatro figuras, sem subir contêiner.
    comuns = [
        "--anel-nos",
        str(csv_anel),
        "--doispc-latencia",
        str(csv_latencia),
        "--doispc-recuperacao",
        str(csv_recuperacao),
        "--bully-nos",
        str(csv_bully),
        "--dir-dados",
        str(dir_dados),
        "--dir-figuras",
        str(dir_figuras),
    ]
    assert analisar.main(["anel", *comuns]) == 0
    assert analisar.main(["2pc-latencia", *comuns]) == 0
    assert analisar.main(["2pc-recuperacao", *comuns]) == 0
    assert analisar.main(["bully-anel", *comuns]) == 0
    assert (dir_figuras / "anel_convergencia.png").exists()
    assert (dir_figuras / "doispc_latencia.png").exists()
    assert (dir_figuras / "doispc_recuperacao.png").exists()
    assert (dir_figuras / "anel_bully.png").exists()

    # 3) Verificação: sobre logs corretos, sai 0; sobre results/ sem logs, também.
    assert verificar.verificar([results], imprimir=lambda *_: None) == 0
    logs = results / "cenario_anel_normal_logs" / "no-anel-1" / "eventos.jsonl"
    _log_anel_convergido(logs)
    assert verificar.verificar([results], imprimir=lambda *_: None) == 0
