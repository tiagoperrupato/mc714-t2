"""CLI de análise do T2: lê os CSVs da bateria e produz as tabelas e figuras de `docs/`.

Não executa Réplica nem sobe contêiner (Requisito 11.1): cada subcomando lê os
CSVs de `results/`, imprime a tabela markdown pronta para colar e grava a figura
correspondente. O subcomando `conferir` reprova, com código diferente de zero,
quando uma tabela de `docs/` fica defasada em relação aos dados atuais
(Requisitos 11.4, 11.5).

Subcomandos:

    anel              convergência do Anel × número de Nós (tabela + figura)
    2pc-latencia      Latencia_de_Commit do 2PC × latência de rede (p50/p95/p99)
    2pc-recuperacao   Tempo_de_Recuperacao sob falha × latência de rede
    conferir          confere se as tabelas de docs/ batem com os dados atuais
    tudo              executa todos os subcomandos acima

A conferência é literal: cada bloco é regerado e o texto exato precisa aparecer
no documento. Um dígito fora do lugar reprova, o que garante que nenhuma tabela
de `docs/` seja digitada à mão (Requisito 11.5).
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
    "2pc-latencia": "python scripts/analisar.py 2pc-latencia",
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
CSV_2PC_LATENCIA = dados.DIR_DADOS / "doispc_latencia.csv"
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
        help=f"CSV da bateria do 2PC por latência de rede "
        f"(padrão: {dados.relativo(dados.BATERIA_2PC_LATENCIA)})",
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
        "2pc-latencia",
        parents=[comum],
        help="Latencia_de_Commit do 2PC × latência de rede (p50/p95/p99)",
    )
    sub.add_parser(
        "2pc-recuperacao",
        parents=[comum],
        help="Tempo_de_Recuperacao do 2PC sob falha × latência de rede",
    )
    sub.add_parser(
        "conferir",
        parents=[comum],
        help="confere se as tabelas de docs/ batem com os dados atuais",
    )
    sub.add_parser("tudo", parents=[comum], help="executa todos os subcomandos")
    return parser


def _carregar(args) -> tuple:
    """Carrega os dois CSVs que a análise consome, em dataframes."""
    df_anel = dados.carregar_anel(args.anel_nos)
    df_2pc = dados.carregar_2pc(args.doispc_latencia)
    return df_anel, df_2pc


def _executar_anel(df_anel, _df_2pc, args) -> int:
    tabela = anel_exp.consolidar(df_anel)
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


def _executar_2pc_latencia(_df_anel, df_2pc, args) -> int:
    tabela = doispc_exp.consolidar_latencia(df_2pc)
    if tabela.empty:
        print(
            "erro: nenhuma configuração do 2PC para consolidar a latência de "
            "commit. Rode a bateria: "
            "python scripts/bateria.py --algoritmo 2pc --variar latencia_rede",
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


def _executar_2pc_recuperacao(_df_anel, df_2pc, args) -> int:
    tabela = doispc_exp.consolidar_recuperacao(df_2pc)
    if tabela.empty:
        print(
            "erro: nenhuma configuração do 2PC para consolidar o tempo de "
            "recuperação. Rode a bateria: "
            "python scripts/bateria.py --algoritmo 2pc --variar latencia_rede",
            file=sys.stderr,
        )
        return 1
    print(
        doispc_exp.recuperacao_como_markdown(
            tabela, COMANDOS["2pc-recuperacao"], _fontes(args.doispc_latencia)
        )
    )
    _gravar_csv(
        tabela, args.dir_dados / CSV_2PC_RECUPERACAO.name, "Recuperação do 2PC"
    )
    destino = args.dir_figuras / FIG_2PC_RECUPERACAO.name
    doispc_exp.gerar_grafico_recuperacao(tabela, destino)
    print(f"Figura gravada em: {dados.relativo(destino)}", file=sys.stderr)
    return 0


def _blocos_conferiveis(df_anel, df_2pc, args) -> list[Bloco]:
    """Os blocos esperados em cada documento. Toda tabela nova de `docs/` entra aqui."""
    return [
        Bloco(
            documento=DOC_ANEL,
            rotulo="convergência do Anel",
            texto=anel_exp.como_markdown(
                anel_exp.consolidar(df_anel), COMANDOS["anel"], _fontes(args.anel_nos)
            ),
            comando=COMANDOS["anel"],
        ),
        Bloco(
            documento=DOC_2PC,
            rotulo="latência de commit",
            texto=doispc_exp.latencia_como_markdown(
                doispc_exp.consolidar_latencia(df_2pc),
                COMANDOS["2pc-latencia"],
                _fontes(args.doispc_latencia),
            ),
            comando=COMANDOS["2pc-latencia"],
        ),
        Bloco(
            documento=DOC_2PC,
            rotulo="tempo de recuperação",
            texto=doispc_exp.recuperacao_como_markdown(
                doispc_exp.consolidar_recuperacao(df_2pc),
                COMANDOS["2pc-recuperacao"],
                _fontes(args.doispc_latencia),
            ),
            comando=COMANDOS["2pc-recuperacao"],
        ),
    ]


def _executar_conferir(df_anel, df_2pc, args) -> int:
    return 0 if relatar([conferir_bloco(b) for b in _blocos_conferiveis(df_anel, df_2pc, args)]) else 1


def main(argv: list[str] | None = None) -> int:
    """Devolve 0 em sucesso e 1 quando os dados são inválidos ou um bloco está defasado."""
    args = _construir_parser().parse_args(argv)

    try:
        df_anel, df_2pc = _carregar(args)
    except DadosError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1

    acoes = {
        "anel": _executar_anel,
        "2pc-latencia": _executar_2pc_latencia,
        "2pc-recuperacao": _executar_2pc_recuperacao,
        "conferir": _executar_conferir,
    }
    if args.comando == "tudo":
        for nome, acao in acoes.items():
            codigo = acao(df_anel, df_2pc, args)
            if codigo != 0:
                print(f"erro: subcomando '{nome}' falhou.", file=sys.stderr)
                return codigo
        return 0
    return acoes[args.comando](df_anel, df_2pc, args)


if __name__ == "__main__":
    raise SystemExit(main())
