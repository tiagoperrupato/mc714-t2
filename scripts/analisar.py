"""CLI de análise do T2: lê os CSVs da bateria e produz as tabelas e figuras de `docs/`.

Não executa Réplica nem sobe contêiner: cada subcomando lê os
CSVs de `results/`, imprime a tabela markdown pronta para colar e grava a figura
correspondente. O subcomando `conferir` reprova, com código diferente de zero,
quando uma tabela de `docs/` fica defasada em relação aos dados atuais.

Subcomandos:

    anel              convergência do Anel × número de Nós (tabela + figura)
    anel-complexidade mensagens do Anel medidas contra 2n − 1, com e sem perda
    2pc-latencia      latência de commit do 2PC × latência de rede (p50/p95/p99)
    2pc-complexidade  mensagens e latência do 2PC medidas contra 4N e 2N atrasos de ida
    2pc-recuperacao   tempo de recuperação após o crash do Coordenador × latência de rede
    conferir          confere se as tabelas de docs/ batem com os dados atuais
    tudo              executa todos os subcomandos acima

A conferência é literal: cada bloco é regerado e o texto exato precisa aparecer
no documento. Um dígito fora do lugar reprova, o que garante que nenhuma tabela
de `docs/` seja digitada à mão.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Torna `src` importável quando o script roda de qualquer diretório.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.analise import anel_exp, dados, doispc_exp  # noqa: E402
from src.analise.dados import DadosError  # noqa: E402

# Comandos citados no rodapé das tabelas e na mensagem da conferência.
COMANDOS: dict[str, str] = {
    "anel": "python scripts/analisar.py anel",
    "anel-complexidade": "python scripts/analisar.py anel-complexidade",
    "2pc-latencia": "python scripts/analisar.py 2pc-latencia",
    "2pc-complexidade": "python scripts/analisar.py 2pc-complexidade",
    "2pc-recuperacao": "python scripts/analisar.py 2pc-recuperacao",
}

# Documentos onde cada tabela é colada.
DOC_ANEL = dados.DIR_DOCS / "resultados-anel.md"
DOC_2PC = dados.DIR_DOCS / "resultados-2pc.md"

# Figuras versionadas.
FIG_ANEL = dados.DIR_FIGURAS / "anel_convergencia.png"
FIG_2PC_LATENCIA = dados.DIR_FIGURAS / "doispc_latencia.png"
FIG_2PC_RECUPERACAO = dados.DIR_FIGURAS / "doispc_recuperacao.png"

# CSVs de análise, versionados junto dos documentos que as citam.
CSV_ANEL = dados.DIR_DADOS / "anel_convergencia.csv"
CSV_ANEL_COMPLEXIDADE = dados.DIR_DADOS / "anel_complexidade.csv"
CSV_2PC_LATENCIA = dados.DIR_DADOS / "doispc_latencia.csv"
CSV_2PC_COMPLEXIDADE = dados.DIR_DADOS / "doispc_complexidade.csv"
CSV_2PC_RECUPERACAO = dados.DIR_DADOS / "doispc_recuperacao.csv"


@dataclass(frozen=True)
class Bloco:
    """Um bloco que deve aparecer literalmente num documento, e o comando que o regenera."""

    documento: Path
    rotulo: str
    texto: str
    comando: str


@dataclass(frozen=True)
class Resultado:
    """Veredito da conferência de um bloco."""

    bloco: Bloco
    ok: bool
    motivo: str


def _normalizar(texto: str) -> str:
    """Ignora CRLF e espaços no fim das linhas, que editores mudam sem avisar."""
    linhas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(linha.rstrip() for linha in linhas)


def conferir_bloco(bloco: Bloco) -> Resultado:
    """Verifica se o documento contém o bloco esperado, literalmente."""
    if not bloco.documento.exists():
        return Resultado(
            bloco, False, f"documento não encontrado: {dados.relativo(bloco.documento)}"
        )
    try:
        conteudo = bloco.documento.read_text(encoding="utf-8")
    except OSError as erro:
        return Resultado(bloco, False, f"falha ao ler o documento: {erro}")
    if _normalizar(bloco.texto) in _normalizar(conteudo):
        return Resultado(bloco, True, "em dia")
    return Resultado(
        bloco,
        False,
        "a tabela no documento não corresponde aos dados atuais. "
        f"Regenere e cole novamente: {bloco.comando}",
    )


def relatar(resultados: list[Resultado]) -> bool:
    """Imprime o relatório da conferência e devolve True se tudo passou."""
    print("\n=== Conferência: tabelas dos documentos x dados atuais ===\n")
    if not resultados:
        print("  (nenhum bloco registrado para conferência)")
        return True
    largura = max(
        len(f"{dados.relativo(r.bloco.documento)} [{r.bloco.rotulo}]") for r in resultados
    )
    for r in resultados:
        identificacao = f"{dados.relativo(r.bloco.documento)} [{r.bloco.rotulo}]"
        marca = "OK    " if r.ok else "FALHA "
        print(f"  {marca} {identificacao.ljust(largura)}  {r.motivo}")
    falhas = [r for r in resultados if not r.ok]
    print()
    if falhas:
        print(f"{len(falhas)} de {len(resultados)} bloco(s) defasado(s).")
        return False
    print(f"Todos os {len(resultados)} bloco(s) em dia.")
    return True


def _fontes(*caminhos: Path) -> list[str]:
    """Arquivos de dados citados no rodapé das tabelas, relativos à raiz."""
    return [dados.relativo(c) for c in caminhos]


def _gravar_csv(tabela, destino: Path, rotulo: str) -> None:
    """Grava um CSV de análise e anuncia o caminho em stderr."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(destino, index=False)
    print(f"{rotulo} gravado em: {dados.relativo(destino)}", file=sys.stderr)


def _opcoes_comuns() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument(
        "--anel-nos",
        type=Path,
        default=dados.BATERIA_ANEL_NOS,
        help=f"CSV da bateria do Anel por número de Nós "
        f"(padrão: {dados.relativo(dados.BATERIA_ANEL_NOS)})",
    )
    comum.add_argument(
        "--doispc-latencia",
        type=Path,
        default=dados.BATERIA_2PC_LATENCIA,
        help=f"CSV de latência do 2PC, uma linha por transação "
        f"(padrão: {dados.relativo(dados.BATERIA_2PC_LATENCIA)})",
    )
    comum.add_argument(
        "--doispc-recuperacao",
        type=Path,
        default=dados.BATERIA_2PC_RECUPERACAO,
        help=f"CSV de recuperação do 2PC, uma linha por crash do Coordenador "
        f"(padrão: {dados.relativo(dados.BATERIA_2PC_RECUPERACAO)})",
    )
    comum.add_argument(
        "--dir-dados",
        type=Path,
        default=dados.DIR_DADOS,
        help=f"diretório dos CSVs de análise (padrão: {dados.relativo(dados.DIR_DADOS)})",
    )
    comum.add_argument(
        "--dir-figuras",
        type=Path,
        default=dados.DIR_FIGURAS,
        help=f"diretório das figuras (padrão: {dados.relativo(dados.DIR_FIGURAS)})",
    )
    return comum


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analisar.py",
        description="Analisa os resultados do T2: tabelas markdown, CSVs e figuras.",
    )
    comum = _opcoes_comuns()
    sub = parser.add_subparsers(dest="comando", required=True, metavar="comando")
    sub.add_parser(
        "anel", parents=[comum], help="convergência do Anel × número de Nós"
    )
    sub.add_parser(
        "anel-complexidade",
        parents=[comum],
        help="mensagens do Anel medidas contra 2n − 1, com e sem perda",
    )
    sub.add_parser(
        "2pc-latencia",
        parents=[comum],
        help="latência de commit do 2PC × latência de rede (p50/p95/p99)",
    )
    sub.add_parser(
        "2pc-complexidade",
        parents=[comum],
        help="mensagens e latência do 2PC medidas contra 4N e 2N atrasos de ida",
    )
    sub.add_parser(
        "2pc-recuperacao",
        parents=[comum],
        help="tempo de recuperação do 2PC após o crash do Coordenador × latência de rede",
    )
    sub.add_parser(
        "conferir",
        parents=[comum],
        help="confere se as tabelas de docs/ batem com os dados atuais",
    )
    sub.add_parser("tudo", parents=[comum], help="executa todos os subcomandos")
    return parser


def _carregar(args) -> dict:
    """Carrega os CSVs que a análise consome, em dataframes indexados pelo subcomando."""
    return {
        "anel": dados.carregar_anel(args.anel_nos),
        "2pc-latencia": dados.carregar_2pc_latencia(args.doispc_latencia),
        "2pc-recuperacao": dados.carregar_2pc_recuperacao(args.doispc_recuperacao),
    }


def _executar_anel(dfs, args) -> int:
    tabela = anel_exp.consolidar(dfs["anel"])
    if tabela.empty:
        print(
            "erro: nenhuma configuração do Anel com réplicas convergidas para "
            "consolidar. Rode a bateria: python scripts/bateria.py --algoritmo anel",
            file=sys.stderr,
        )
        return 1
    print(anel_exp.como_markdown(tabela, COMANDOS["anel"], _fontes(args.anel_nos)))
    _gravar_csv(tabela, args.dir_dados / CSV_ANEL.name, "Convergência do Anel")
    destino = args.dir_figuras / FIG_ANEL.name
    anel_exp.gerar_grafico(tabela, destino)
    print(f"Figura gravada em: {dados.relativo(destino)}", file=sys.stderr)
    return 0


def _executar_anel_complexidade(dfs, args) -> int:
    tabela = anel_exp.consolidar(dfs["anel"])
    if tabela.empty:
        print(
            "erro: nenhuma configuração do Anel com réplicas convergidas para "
            "comparar. Rode a bateria: python scripts/bateria.py --algoritmo anel",
            file=sys.stderr,
        )
        return 1
    print(
        anel_exp.complexidade_como_markdown(
            tabela, COMANDOS["anel-complexidade"], _fontes(args.anel_nos)
        )
    )
    _gravar_csv(tabela, args.dir_dados / CSV_ANEL_COMPLEXIDADE.name, "Complexidade do Anel")
    return 0


def _executar_2pc_complexidade(dfs, args) -> int:
    tabela = doispc_exp.consolidar_complexidade(dfs["2pc-latencia"])
    if tabela.empty:
        print(
            "erro: nenhuma transação do 2PC com COMMIT fora do aquecimento para "
            "comparar. Rode a bateria: python scripts/bateria.py --algoritmo 2pc",
            file=sys.stderr,
        )
        return 1
    print(
        doispc_exp.complexidade_como_markdown(
            tabela, COMANDOS["2pc-complexidade"], _fontes(args.doispc_latencia)
        )
    )
    _gravar_csv(tabela, args.dir_dados / CSV_2PC_COMPLEXIDADE.name, "Complexidade do 2PC")
    return 0


def _executar_2pc_latencia(dfs, args) -> int:
    tabela = doispc_exp.consolidar_latencia(dfs["2pc-latencia"])
    if tabela.empty:
        print(
            "erro: nenhuma transação do 2PC com COMMIT fora do aquecimento para "
            "consolidar. Rode a bateria: python scripts/bateria.py --algoritmo 2pc",
            file=sys.stderr,
        )
        return 1
    print(
        doispc_exp.latencia_como_markdown(
            tabela, COMANDOS["2pc-latencia"], _fontes(args.doispc_latencia)
        )
    )
    _gravar_csv(tabela, args.dir_dados / CSV_2PC_LATENCIA.name, "Latência de commit do 2PC")
    destino = args.dir_figuras / FIG_2PC_LATENCIA.name
    doispc_exp.gerar_grafico_latencia(tabela, destino)
    print(f"Figura gravada em: {dados.relativo(destino)}", file=sys.stderr)
    return 0


def _executar_2pc_recuperacao(dfs, args) -> int:
    tabela = doispc_exp.consolidar_recuperacao(dfs["2pc-recuperacao"])
    if tabela.empty:
        print(
            "erro: nenhuma recuperação do 2PC terminada em COMMIT para consolidar. "
            "Rode a bateria: python scripts/bateria.py --algoritmo 2pc",
            file=sys.stderr,
        )
        return 1
    print(
        doispc_exp.recuperacao_como_markdown(
            tabela, COMANDOS["2pc-recuperacao"], _fontes(args.doispc_recuperacao)
        )
    )
    _gravar_csv(
        tabela, args.dir_dados / CSV_2PC_RECUPERACAO.name, "Recuperação do 2PC"
    )
    destino = args.dir_figuras / FIG_2PC_RECUPERACAO.name
    doispc_exp.gerar_grafico_recuperacao(tabela, destino)
    print(f"Figura gravada em: {dados.relativo(destino)}", file=sys.stderr)
    return 0


def _blocos_conferiveis(dfs, args) -> list[Bloco]:
    """Os blocos esperados em cada documento. Toda tabela nova de `docs/` entra aqui."""
    return [
        Bloco(
            documento=DOC_ANEL,
            rotulo="convergência do Anel",
            texto=anel_exp.como_markdown(
                anel_exp.consolidar(dfs["anel"]), COMANDOS["anel"], _fontes(args.anel_nos)
            ),
            comando=COMANDOS["anel"],
        ),
        Bloco(
            documento=DOC_ANEL,
            rotulo="mensagens do Anel × esperado",
            texto=anel_exp.complexidade_como_markdown(
                anel_exp.consolidar(dfs["anel"]),
                COMANDOS["anel-complexidade"],
                _fontes(args.anel_nos),
            ),
            comando=COMANDOS["anel-complexidade"],
        ),
        Bloco(
            documento=DOC_2PC,
            rotulo="mensagens e latência × esperado",
            texto=doispc_exp.complexidade_como_markdown(
                doispc_exp.consolidar_complexidade(dfs["2pc-latencia"]),
                COMANDOS["2pc-complexidade"],
                _fontes(args.doispc_latencia),
            ),
            comando=COMANDOS["2pc-complexidade"],
        ),
        Bloco(
            documento=DOC_2PC,
            rotulo="latência de commit",
            texto=doispc_exp.latencia_como_markdown(
                doispc_exp.consolidar_latencia(dfs["2pc-latencia"]),
                COMANDOS["2pc-latencia"],
                _fontes(args.doispc_latencia),
            ),
            comando=COMANDOS["2pc-latencia"],
        ),
        Bloco(
            documento=DOC_2PC,
            rotulo="tempo de recuperação",
            texto=doispc_exp.recuperacao_como_markdown(
                doispc_exp.consolidar_recuperacao(dfs["2pc-recuperacao"]),
                COMANDOS["2pc-recuperacao"],
                _fontes(args.doispc_recuperacao),
            ),
            comando=COMANDOS["2pc-recuperacao"],
        ),
    ]


def _executar_conferir(dfs, args) -> int:
    return 0 if relatar([conferir_bloco(b) for b in _blocos_conferiveis(dfs, args)]) else 1


def main(argv: list[str] | None = None) -> int:
    """Devolve 0 em sucesso e 1 quando os dados são inválidos ou um bloco está defasado."""
    args = _construir_parser().parse_args(argv)

    try:
        dfs = _carregar(args)
    except DadosError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1

    acoes = {
        "anel": _executar_anel,
        "anel-complexidade": _executar_anel_complexidade,
        "2pc-latencia": _executar_2pc_latencia,
        "2pc-complexidade": _executar_2pc_complexidade,
        "2pc-recuperacao": _executar_2pc_recuperacao,
        "conferir": _executar_conferir,
    }
    if args.comando == "tudo":
        for nome, acao in acoes.items():
            codigo = acao(dfs, args)
            if codigo != 0:
                print(f"erro: subcomando '{nome}' falhou.", file=sys.stderr)
                return codigo
        return 0
    return acoes[args.comando](dfs, args)


if __name__ == "__main__":
    raise SystemExit(main())
