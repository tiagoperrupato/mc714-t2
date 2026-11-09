"""Carga e validação dos CSVs da bateria, com mensagem clara em vez de `KeyError`.

A análise lê exclusivamente os CSVs de `results/` produzidos por
`scripts/bateria.py` (Requisito 11.1): nunca sobe contêiner nem reexecuta uma
Réplica. Este módulo concentra os caminhos padrão, a leitura validada (arquivo
presente, colunas obrigatórias, chave única por Réplica) e os helpers de recorte
por nível de parâmetro usados pelas análises de cada algoritmo.

A raiz do projeto e os destinos de `results/`, `docs/dados/` e
`docs/figuras/` são derivados aqui a partir da localização deste arquivo, sem
depender do diretório de trabalho.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Raiz do projeto T2: o diretório que contém `src/`, `scripts/`, `docs/`.
RAIZ: Path = Path(__file__).resolve().parents[2]

DIR_RESULTS: Path = RAIZ / "results"
DIR_DOCS: Path = RAIZ / "docs"
DIR_DADOS: Path = DIR_DOCS / "dados"
DIR_FIGURAS: Path = DIR_DOCS / "figuras"

# CSVs produzidos pela bateria (scripts/bateria.py). Os do 2PC são um por transação e um por crash
# do Coordenador.
BATERIA_ANEL_NOS: Path = DIR_RESULTS / "bateria_anel_num_nos.csv"
BATERIA_ANEL_FALHAS: Path = DIR_RESULTS / "bateria_anel_taxa_falhas.csv"
BATERIA_2PC_LATENCIA: Path = DIR_RESULTS / "bateria_2pc_latencia_rede.csv"
BATERIA_2PC_RECUPERACAO: Path = DIR_RESULTS / "bateria_2pc_recuperacao.csv"

# Para casar um nível float (num_nos lido como 5.0, taxa 0.10) com o que o
# usuário digita na CLI.
TOLERANCIA: float = 1e-9

# Colunas mínimas exigidas em cada esquema, conforme grava a bateria do T2. As
# colunas de ambiente (amb_*) existem mas não são validadas aqui, pois são
# carimbos de reprodutibilidade e não entram no cálculo.
COLUNAS_ANEL: tuple[str, ...] = (
    "algoritmo",
    "parametro_variado",
    "semente",
    "num_nos",
    "taxa_falhas",
    "tempo_convergencia",
    "mensagens",
    "convergiu",
    "nos_vivos",
)

COLUNAS_2PC_LATENCIA: tuple[str, ...] = (
    "algoritmo",
    "parametro_variado",
    "semente",
    "num_participantes",
    "latencia_rede",
    "aquecimento",
    "transacao",
    "decisao",
    "latencia_commit_s",
    "mensagens",
)

COLUNAS_2PC_RECUPERACAO: tuple[str, ...] = (
    "algoritmo",
    "parametro_variado",
    "semente",
    "latencia_rede",
    "transacao",
    "decisao",
    "em_duvida",
    "tempo_recuperacao_s",
    "mensagens",
)

CHAVE_REPLICA: tuple[str, ...] = ("algoritmo", "parametro_variado", "semente")
# O id de transação carrega a Semente da Réplica, então ele sozinho identifica a linha.
CHAVE_TRANSACAO: tuple[str, ...] = ("transacao",)


class DadosError(Exception):
    """Erro na carga ou na validação de um CSV de entrada da análise."""


def relativo(caminho: Path) -> str:
    """`caminho` relativo à raiz, para exibição, ou o absoluto se estiver fora dela."""
    try:
        return str(caminho.resolve().relative_to(RAIZ))
    except ValueError:
        return str(caminho)


def ler_csv(caminho: Path, colunas: tuple[str, ...], rotulo: str) -> pd.DataFrame:
    """Lê um CSV, levantando `DadosError` se ele faltar, não abrir ou não tiver `colunas`."""
    if not caminho.exists():
        raise DadosError(
            f"arquivo de {rotulo} não encontrado: {relativo(caminho)}. "
            "Rode a bateria antes: python scripts/bateria.py"
        )
    try:
        df = pd.read_csv(caminho)
    except pd.errors.EmptyDataError as erro:
        raise DadosError(f"arquivo de {rotulo} vazio: {relativo(caminho)}") from erro
    except (OSError, pd.errors.ParserError) as erro:
        raise DadosError(
            f"falha ao ler o arquivo de {rotulo} {relativo(caminho)}: {erro}"
        ) from erro

    faltando = [c for c in colunas if c not in df.columns]
    if faltando:
        raise DadosError(
            f"arquivo de {rotulo} {relativo(caminho)} sem as colunas "
            f"obrigatórias: {', '.join(faltando)}. "
            "O arquivo pode ser de uma versão anterior da bateria; "
            "regenere com python scripts/bateria.py"
        )
    if df.empty:
        raise DadosError(f"arquivo de {rotulo} sem nenhuma linha: {relativo(caminho)}")
    return df


def rejeitar_duplicadas(
    df: pd.DataFrame, chave: tuple[str, ...], caminho: Path
) -> None:
    """Levanta `DadosError` se alguma chave de Réplica aparecer mais de uma vez.

    Uma Réplica contada duas vezes desloca a média e estreita o IC, então é um
    erro, não um aviso.
    """
    presentes = tuple(c for c in chave if c in df.columns)
    if not presentes:
        return
    duplicadas = df[df.duplicated(list(presentes), keep=False)]
    if duplicadas.empty:
        return
    raise DadosError(
        f"{len(duplicadas)} linhas com a chave ({', '.join(presentes)}) repetida "
        f"em {relativo(caminho)}. Uma réplica contada duas vezes desloca a média "
        "e estreita o IC. Regenere com python scripts/bateria.py"
    )


def carregar_anel(caminho: Path) -> pd.DataFrame:
    """Carrega um CSV da bateria do Anel, validando colunas e unicidade por Réplica."""
    df = ler_csv(caminho, COLUNAS_ANEL, "bateria do Anel")
    rejeitar_duplicadas(df, CHAVE_REPLICA, caminho)
    return df


def carregar_2pc_latencia(caminho: Path) -> pd.DataFrame:
    """Carrega o CSV de latência do 2PC, validando colunas e unicidade por transação."""
    df = ler_csv(caminho, COLUNAS_2PC_LATENCIA, "latência do 2PC")
    rejeitar_duplicadas(df, CHAVE_TRANSACAO, caminho)
    return df


def carregar_2pc_recuperacao(caminho: Path) -> pd.DataFrame:
    """Carrega o CSV de recuperação do 2PC, validando colunas e unicidade por Réplica."""
    df = ler_csv(caminho, COLUNAS_2PC_RECUPERACAO, "recuperação do 2PC")
    rejeitar_duplicadas(df, CHAVE_REPLICA, caminho)
    return df


def filtrar_nivel(
    df: pd.DataFrame, coluna: str, valor: float, tolerancia: float = TOLERANCIA
) -> pd.DataFrame:
    """As linhas de um nível de parâmetro.

    Levanta `DadosError` listando os níveis disponíveis se nenhum casar, para que
    um nível ausente não vire uma tabela silenciosamente vazia.
    """
    if coluna not in df.columns:
        raise DadosError(f"coluna {coluna!r} ausente no arquivo carregado")
    recorte = df[(df[coluna].astype(float) - valor).abs() <= tolerancia]
    if recorte.empty:
        disponiveis = ", ".join(f"{v:g}" for v in sorted(df[coluna].astype(float).unique()))
        raise DadosError(
            f"nenhuma linha com {coluna} = {valor:g}; os níveis presentes são: "
            f"{disponiveis}."
        )
    return recorte
