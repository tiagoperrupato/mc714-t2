"""Demo de ponta a ponta dos cenários de falha (tarefa 12).

Roda os dois cenários de falha exigidos pelo enunciado, em sequência, sobre o
Docker Compose, e deixa os logs coletados sob `results/` já conferidos pelos
invariantes de correção:

1. **Anel — morte do Líder → reeleição** (`anel falha`, Requisito 8.6): elege o
   maior id (5), mata o Líder e mostra os Nós vivos reelegendo o maior id vivo
   (4).
2. **2PC, crash e recuperação do Coordenador** (`2pc crash-coord`,
   Requisito 8.5): todos votam SIM, o processo do Coordenador cai logo depois
   de gravar a Decisao, e os Participantes ficam em Estado_Em_Duvida. O
   contêiner reinicia, o Coordenador relê o log e todos decidem COMMIT.

Cada cenário já roda `scripts/verificar.py` sobre os seus próprios logs ao final
(ver `scripts/cenario.py`), confirmando a correção. Esta demo apenas encadeia os
dois e, no fim, roda a verificação de novo sobre TODOS os logs coletados dos dois
cenários juntos, deixando um relatório único.

Este é o mesmo roteiro que o vídeo de demonstração da dupla mostra. Requer Docker
com Compose v2; sem Docker, sai com código 2 e uma mensagem clara, sem travar.

Uso:

    python scripts/demo.py
        Roda anel/falha e 2pc/crash-coord e verifica tudo.

    python scripts/demo.py --semente 7 --results results/demo
        Fixa a Semente dos cenários e grava num diretório próprio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import cenario, verificar  # noqa: E402

RESULTS_PADRAO = _RAIZ / "results"

# Os dois cenários de falha da demo, na ordem em que o vídeo os mostra.
CENARIOS_FALHA = (
    ("anel", "falha"),
    ("2pc", "crash-coord"),
)


def executar_demo(
    results: Path,
    *,
    semente: int = cenario.SEMENTE_PADRAO,
    saida=sys.stdout,
    erro=sys.stderr,
) -> int:
    """Roda os cenários de falha em sequência e verifica os logs coletados.

    Devolve 0 quando todos os cenários rodam e nenhum invariante de safety
    reprova (avisos não reprovam), 1 em falha de
    execução ou reprovação, e 2 quando o Docker está indisponível.
    """
    if not cenario.docker_disponivel():
        print(
            "[demo] Docker (daemon + compose v2) indisponível; a demo exige "
            "contêineres. As provas equivalentes sem Docker estão em "
            "tests/anel/test_falhas_rede.py e tests/doispc/test_falhas_rede.py.",
            file=erro,
        )
        return 2

    base = Path(results)
    base.mkdir(parents=True, exist_ok=True)

    for algoritmo, modo in CENARIOS_FALHA:
        print(f"\n[demo] === cenário de falha: {algoritmo}/{modo} ===", file=saida)
        codigo = cenario.executar_cenario(
            algoritmo, modo, base, semente=semente, saida=saida, erro=erro
        )
        if codigo == 2:
            return 2
        if codigo != 0:
            print(f"[demo] cenário {algoritmo}/{modo} falhou (código {codigo})", file=erro)
            return 1

    # Verificação final sobre TODOS os logs coletados pela demo, de uma vez.
    print("\n[demo] === verificação consolidada dos logs coletados ===", file=saida)
    linhas: list[str] = []
    codigo_verif = verificar.verificar([base], imprimir=linhas.append)
    print("\n".join(linhas), file=saida)
    if codigo_verif != 0:
        print("[demo] a verificação consolidada REPROVOU", file=erro)
        return 1
    print("\n[demo] correção confirmada: nenhuma violação de safety.", file=saida)
    return 0


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--semente",
        type=int,
        default=cenario.SEMENTE_PADRAO,
        help=f"Semente dos cenários (default: {cenario.SEMENTE_PADRAO})",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS_PADRAO,
        help=f"diretório de saída (default: {RESULTS_PADRAO})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    return executar_demo(Path(args.results), semente=args.semente)


if __name__ == "__main__":
    raise SystemExit(main())
