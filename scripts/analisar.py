"""CLI de análise do T2: lê os CSVs das baterias e produz as tabelas e figuras de `docs/`.

Não executa Réplica nem sobe contêiner. Cada linha de `TABELAS` é um subcomando, que imprime a
tabela markdown pronta para colar e grava o CSV consolidado e, quando há, a figura. `conferir`
sai 1 quando uma tabela de `docs/` fica defasada em relação aos dados atuais, e `tudo` roda todos,
com o `conferir` por último. `python scripts/analisar.py --help` lista os subcomandos.

A conferência é literal: cada bloco é regerado e o texto exato precisa aparecer
no documento. Um dígito fora do lugar reprova, o que garante que nenhuma tabela
de `docs/` seja digitada à mão.
"""

from __future__ import annotations

import argparse
import functools
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

# Torna `src` importável quando o script roda de qualquer diretório.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.analise import anel_exp, bully_exp, dados, doispc_exp  # noqa: E402
from src.analise.dados import DadosError  # noqa: E402

# Documentos onde as tabelas são coladas.
DOC_ANEL = dados.DIR_DOCS / "resultados-anel.md"
DOC_2PC = dados.DIR_DOCS / "resultados-2pc.md"
DOC_BULLY = dados.DIR_DOCS / "resultados-bully.md"


@dataclass(frozen=True)
class Entrada:
    """CSV de uma bateria: a opção que o aponta, o carregador e o aviso de tabela vazia."""

    argumento: str
    padrao: Path
    ajuda: str
    carregar: Callable[[Path], pd.DataFrame]
    vazia: str
    bateria: str

    @property
    def opcao(self) -> str:
        return "--" + self.argumento.replace("_", "-")


_ENTRADAS: dict[str, Entrada] = {
    "anel": Entrada(
        argumento="anel_nos",
        padrao=dados.BATERIA_ANEL_NOS,
        ajuda="CSV da bateria do Anel por número de Nós",
        carregar=dados.carregar_anel,
        vazia="nenhuma configuração do Anel com réplicas convergidas",
        bateria="python scripts/bateria.py --algoritmo anel",
    ),
    "bully": Entrada(
        argumento="bully_nos",
        padrao=dados.BATERIA_BULLY_NOS,
        ajuda="CSV da bateria do Bully por número de Nós",
        carregar=dados.carregar_bully,
        vazia="nenhuma configuração do Bully com réplicas convergidas",
        bateria="python scripts/bateria.py --algoritmo bully",
    ),
    "2pc-latencia": Entrada(
        argumento="doispc_latencia",
        padrao=dados.BATERIA_2PC_LATENCIA,
        ajuda="CSV de latência do 2PC, uma linha por transação",
        carregar=dados.carregar_2pc_latencia,
        vazia="nenhuma transação do 2PC com COMMIT fora do aquecimento",
        bateria="python scripts/bateria.py --algoritmo 2pc",
    ),
    "2pc-recuperacao": Entrada(
        argumento="doispc_recuperacao",
        padrao=dados.BATERIA_2PC_RECUPERACAO,
        ajuda="CSV de recuperação do 2PC, uma linha por crash do Coordenador",
        carregar=dados.carregar_2pc_recuperacao,
        vazia="nenhuma recuperação do 2PC terminada em COMMIT",
        bateria="python scripts/bateria.py --algoritmo 2pc",
    ),
}


@dataclass(frozen=True)
class Tabela:
    """Uma tabela de `docs/` e o subcomando que a gera.

    `consolidar` recebe um dataframe por item de `entradas`, na mesma ordem. `nome` é o radical do
    CSV consolidado e da figura.
    """

    comando: str
    ajuda: str
    documento: Path
    rotulo: str
    entradas: tuple[str, ...]
    consolidar: Callable[..., pd.DataFrame]
    markdown: Callable[[pd.DataFrame, str, list[str]], str]
    nome: str
    figura: Optional[Callable[[pd.DataFrame, Path], None]]


TABELAS: tuple[Tabela, ...] = (
    Tabela(
        comando="anel",
        ajuda="convergência do Anel × número de Nós",
        documento=DOC_ANEL,
        rotulo="convergência do Anel",
        entradas=("anel",),
        consolidar=anel_exp.consolidar,
        markdown=anel_exp.como_markdown,
        nome="anel_convergencia",
        figura=anel_exp.gerar_grafico,
    ),
    Tabela(
        comando="anel-complexidade",
        ajuda="mensagens do Anel medidas contra 2n − 1, com e sem perda",
        documento=DOC_ANEL,
        rotulo="mensagens do Anel × esperado",
        entradas=("anel",),
        consolidar=anel_exp.consolidar,
        markdown=anel_exp.complexidade_como_markdown,
        nome="anel_complexidade",
        figura=None,
    ),
    Tabela(
        comando="2pc-latencia",
        ajuda="latência de commit do 2PC × latência de rede (p50/p95/p99)",
        documento=DOC_2PC,
        rotulo="latência de commit",
        entradas=("2pc-latencia",),
        consolidar=doispc_exp.consolidar_latencia,
        markdown=doispc_exp.latencia_como_markdown,
        nome="doispc_latencia",
        figura=doispc_exp.gerar_grafico_latencia,
    ),
    Tabela(
        comando="2pc-complexidade",
        ajuda="mensagens e latência do 2PC medidas contra 4N e 2N atrasos de ida",
        documento=DOC_2PC,
        rotulo="mensagens e latência × esperado",
        entradas=("2pc-latencia",),
        consolidar=doispc_exp.consolidar_complexidade,
        markdown=doispc_exp.complexidade_como_markdown,
        nome="doispc_complexidade",
        figura=None,
    ),
    Tabela(
        comando="2pc-recuperacao",
        ajuda="tempo de recuperação do 2PC após o crash do Coordenador × latência de rede",
        documento=DOC_2PC,
        rotulo="tempo de recuperação",
        entradas=("2pc-recuperacao",),
        consolidar=doispc_exp.consolidar_recuperacao,
        markdown=doispc_exp.recuperacao_como_markdown,
        nome="doispc_recuperacao",
        figura=doispc_exp.gerar_grafico_recuperacao,
    ),
    Tabela(
        comando="bully",
        ajuda="mensagens do Bully medidas contra n − 1 e n² − 1, com perda",
        documento=DOC_BULLY,
        rotulo="mensagens do Bully × esperado",
        entradas=("bully",),
        consolidar=bully_exp.consolidar,
        markdown=bully_exp.complexidade_como_markdown,
        nome="bully_complexidade",
        figura=None,
    ),
    Tabela(
        comando="bully-anel",
        ajuda="mensagens e saltos até a convergência, Anel × Bully nas mesmas condições",
        documento=DOC_BULLY,
        rotulo="Anel × Bully",
        entradas=("anel", "bully"),
        consolidar=bully_exp.comparar_com_anel,
        markdown=bully_exp.comparacao_como_markdown,
        nome="anel_bully",
        figura=bully_exp.gerar_grafico,
    ),
)

# Comandos citados no rodapé das tabelas e na mensagem da conferência.
COMANDOS: dict[str, str] = {t.comando: f"python scripts/analisar.py {t.comando}" for t in TABELAS}


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


def _consolidar(tabela: Tabela, dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return tabela.consolidar(*(dfs[e] for e in tabela.entradas))


def _markdown(tabela: Tabela, consolidada: pd.DataFrame, args) -> str:
    """A tabela com o rodapé que cita o comando e os CSVs de onde ela saiu."""
    fontes = [dados.relativo(getattr(args, _ENTRADAS[e].argumento)) for e in tabela.entradas]
    return tabela.markdown(consolidada, COMANDOS[tabela.comando], fontes)


def _opcoes_comuns() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    for entrada in _ENTRADAS.values():
        comum.add_argument(
            entrada.opcao,
            type=Path,
            default=entrada.padrao,
            help=f"{entrada.ajuda} (padrão: {dados.relativo(entrada.padrao)})",
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
    for tabela in TABELAS:
        sub.add_parser(tabela.comando, parents=[comum], help=tabela.ajuda)
    sub.add_parser(
        "conferir",
        parents=[comum],
        help="confere se as tabelas de docs/ batem com os dados atuais",
    )
    sub.add_parser("tudo", parents=[comum], help="executa todos os subcomandos")
    return parser


def _executar(tabela: Tabela, dfs: dict[str, pd.DataFrame], args) -> int:
    """Imprime a tabela e grava o CSV e a figura. Devolve 1 se não há o que consolidar."""
    consolidada = _consolidar(tabela, dfs)
    if consolidada.empty:
        entradas = [_ENTRADAS[e] for e in tabela.entradas]
        print(
            f"erro: {' ou '.join(e.vazia for e in entradas)} para consolidar. "
            f"Rode a bateria: {' e '.join(e.bateria for e in entradas)}",
            file=sys.stderr,
        )
        return 1
    print(_markdown(tabela, consolidada, args))
    destino = args.dir_dados / f"{tabela.nome}.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    consolidada.to_csv(destino, index=False)
    print(f"CSV gravado em: {dados.relativo(destino)}", file=sys.stderr)
    if tabela.figura is not None:
        destino = args.dir_figuras / f"{tabela.nome}.png"
        tabela.figura(consolidada, destino)
        print(f"Figura gravada em: {dados.relativo(destino)}", file=sys.stderr)
    return 0


def _blocos_conferiveis(dfs: dict[str, pd.DataFrame], args) -> list[Bloco]:
    """O bloco que cada tabela de `TABELAS` espera encontrar no seu documento."""
    return [
        Bloco(
            documento=tabela.documento,
            rotulo=tabela.rotulo,
            texto=_markdown(tabela, _consolidar(tabela, dfs), args),
            comando=COMANDOS[tabela.comando],
        )
        for tabela in TABELAS
    ]


def _executar_conferir(dfs: dict[str, pd.DataFrame], args) -> int:
    return 0 if relatar([conferir_bloco(b) for b in _blocos_conferiveis(dfs, args)]) else 1


def main(argv: list[str] | None = None) -> int:
    """Devolve 0 em sucesso e 1 quando os dados são inválidos ou um bloco está defasado."""
    args = _construir_parser().parse_args(argv)

    acoes = {t.comando: functools.partial(_executar, t) for t in TABELAS}
    acoes["conferir"] = _executar_conferir
    try:
        dfs = {
            nome: entrada.carregar(getattr(args, entrada.argumento))
            for nome, entrada in _ENTRADAS.items()
        }
        if args.comando != "tudo":
            return acoes[args.comando](dfs, args)
        for nome, acao in acoes.items():
            codigo = acao(dfs, args)
            if codigo != 0:
                print(f"erro: subcomando '{nome}' falhou.", file=sys.stderr)
                return codigo
    except DadosError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
