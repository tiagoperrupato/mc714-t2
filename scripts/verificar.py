"""Roda os invariantes de correção do T2 sobre os logs coletados.

Lê os Logs_Estruturados (JSONL) e os Logs_Duraveis do 2PC que os Nós reais
deixaram sob `results/`, executa os invariantes de `src.analise.invariantes`
e reporta, por invariante, se passou, reprovou ou emitiu aviso.

Sai com código **diferente de zero apenas quando algum invariante reprova** — ou
seja, quando uma propriedade de *safety* é violada. Um aviso,
como o Estado_Em_Duvida bloqueante do 2PC, aparece no relatório
mas NÃO altera o código de saída, porque bloquear é comportamento correto do
algoritmo. A ausência de dados (nenhum log coletado ainda) também não reprova:
não há violação a relatar, apenas nada a verificar.

Uso:

    python scripts/verificar.py
        Verifica tudo que houver sob `results/`.

    python scripts/verificar.py logs_a/ logs_b/ um_arquivo.log
        Verifica caminhos específicos (arquivos ou diretórios), úteis para
        conferir o resultado de um cenário isolado.

Este comando NÃO re-simula réplica: as
propriedades do T2 são de correção sobre eventos discretos observados nos logs
dos Nós reais, não médias estatísticas de uma bateria. A camada permanece fiel à
regra de ouro — lê dados, não executa Réplica nem sobe contêiner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Torna `src` importável quando o script roda de qualquer diretório.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.analise import invariantes  # noqa: E402

RESULTS_PADRAO = _RAIZ / "results"


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verificar.py",
        description=(
            "Verifica os invariantes de correção do T2 sobre os logs coletados. "
            "Sai 0 quando nenhum invariante reprova (mesmo havendo avisos) e 1 "
            "quando alguma propriedade de safety é violada."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "caminhos",
        nargs="*",
        type=Path,
        help="arquivos ou diretórios de log a verificar (default: results/)",
    )
    return parser


def verificar(caminhos: list[Path], *, imprimir=print) -> int:
    """Carrega os logs dos `caminhos`, roda os invariantes e devolve o código de saída.

    Devolve 0 quando nenhum invariante reprova (incluindo o caso de ausência de
    dados e o de apenas avisos) e 1 quando há ao menos uma reprovação de safety.
    """
    eventos = invariantes.carregar_eventos(*caminhos)
    duraveis = invariantes.carregar_duraveis(*caminhos)

    if not eventos and not duraveis:
        locais = ", ".join(str(c) for c in caminhos)
        imprimir(
            f"nenhum log encontrado em: {locais}\n"
            "nada a verificar. Os cenários coletam os logs: python scripts/cenario.py anel normal"
        )
        return 0

    dados = invariantes.Dados(eventos=eventos, duraveis=duraveis)
    vereditos = invariantes.verificar_todos(dados)
    imprimir(
        f"\nlidos {len(eventos)} evento(s) estruturado(s) e "
        f"{len(duraveis)} entrada(s) de Log_Duravel."
    )
    ok = invariantes.relatar(vereditos, imprimir=imprimir)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    return verificar(list(args.caminhos) or [RESULTS_PADRAO])


if __name__ == "__main__":
    raise SystemExit(main())
