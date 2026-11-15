"""Roda os invariantes de correção sobre os logs dos Nós e devolve o código de saída.

Cada diretório `*_logs` é uma execução e é verificado sozinho, porque os invariantes do Anel tomam
como vivos os Nós que aparecem no log. Um caminho sem `*_logs` dentro é verificado inteiro. Sai 1
quando algum invariante reprova em algum diretório. Aviso e lacuna não mudam o código de saída, a
menos que quem chama exija o invariante, como cada cenário faz com os que ele demonstra.

Uso:

    python scripts/verificar.py                                   # cada diretório *_logs de results/
    python scripts/verificar.py results/cenario_anel_falha_logs   # um diretório só

Lê dados, não executa Réplica nem sobe contêiner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

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
            "Verifica os invariantes de correção do T2 em cada diretório de logs. Sai 0 quando "
            "nenhum invariante reprova, mesmo havendo avisos, e 1 quando algum invariante "
            "reprova em algum diretório."
        ),
    )
    parser.add_argument(
        "caminhos", nargs="*", type=Path, default=[RESULTS_PADRAO],
        help="diretórios ou arquivos de log. Um diretório com *_logs dentro é verificado por "
        "eles (default: results/)",
    )
    return parser


def _unidades(caminhos: list[Path]) -> list[Path]:
    """Os `*_logs` de cada caminho que os tem, ou o próprio caminho, na ordem dada."""
    lista: list[Path] = []
    for caminho in caminhos:
        filhos = sorted(p for p in caminho.glob("*_logs") if p.is_dir()) if caminho.is_dir() else []
        lista.extend(filhos or [caminho])
    return lista


def _verificar_unidade(unidade: Path, imprimir, exigidos: tuple[int, ...]) -> bool:
    """Roda os invariantes sobre os logs de uma execução e devolve se nenhum reprovou."""
    eventos = invariantes.carregar_eventos(unidade)
    duraveis = invariantes.carregar_duraveis(unidade)
    if not eventos and not duraveis:
        imprimir(f"\n=== {unidade.name}: nenhum log ===\n")
        if exigidos:
            numeros = ", ".join(f"{n:02d}" for n in exigidos)
            imprimir(f"Reprovado: {unidade.name} não tem log, e os invariantes {numeros} são exigidos.")
            return False
        imprimir("Nada a verificar. Os cenários coletam os logs: python scripts/cenario.py anel normal")
        return True
    vereditos = invariantes.verificar_todos(invariantes.Dados(eventos=eventos, duraveis=duraveis))
    titulo = f"{unidade.name}: {len(eventos)} evento(s) e {len(duraveis)} entrada(s) de Log_Duravel"
    ok = invariantes.relatar(vereditos, titulo, imprimir=imprimir)
    sem_caso = [v for v in vereditos if v.numero in exigidos and v.verificados == 0]
    for v in sem_caso:
        imprimir(
            f"Reprovado: o invariante {v.numero:02d} ({v.titulo}) é exigido e terminou sem caso "
            f"verificado em {unidade.name}."
        )
    return ok and not sem_caso


def verificar(caminhos: list[Path], *, imprimir=print, exigidos: Iterable[int] = ()) -> int:
    """Verifica cada execução dos `caminhos` sozinha e devolve 1 se alguma reprovou.

    Um invariante de `exigidos` que termina sem caso verificado reprova o diretório.
    """
    exigidos = tuple(exigidos)
    lista = _unidades(caminhos)
    reprovadas = [u.name for u in lista if not _verificar_unidade(u, imprimir, exigidos)]
    resumo = (
        f"{len(reprovadas)} com reprovação: {', '.join(reprovadas)}."
        if reprovadas else "nenhum com reprovação."
    )
    imprimir(f"\n{len(lista)} diretório(s) verificado(s), {resumo}")
    return 1 if reprovadas else 0


def main(argv: list[str] | None = None) -> int:
    return verificar(_construir_parser().parse_args(argv).caminhos)


if __name__ == "__main__":
    raise SystemExit(main())
