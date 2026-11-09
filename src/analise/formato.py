"""Tabelas markdown prontas para colar em `docs/`, com vírgula decimal.

Células já formatadas como texto,
colunas de texto à esquerda e numéricas à direita, e um rodapé de reprodução que
nomeia o comando que regenera a tabela e os arquivos de onde ela saiu. Toda
tabela de `docs/` é produto de um comando da análise, então o
rodapé deixa isso explícito no documento.
"""

from __future__ import annotations

import math

# Valor inexistente, como um percentil de um grupo sem réplicas elegíveis.
AUSENTE: str = "n/d"


def numero(valor: float, casas: int = 4, sinal: bool = False) -> str:
    """Número com vírgula decimal e `casas` fixas, `n/d` se ausente e "+" explícito."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return AUSENTE
    texto = f"{valor:{'+' if sinal else ''}.{casas}f}"
    return texto.replace(".", ",")


def inteiro(valor) -> str:
    """Inteiro, ou `n/d` se ausente."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return AUSENTE
    return str(int(valor))


def tabela_markdown(
    linhas: list[dict[str, str]],
    colunas: list[str],
    colunas_texto: list[str] | None = None,
) -> str:
    """Tabela markdown alinhada, a partir de células já formatadas como texto.

    `colunas_texto` vão à esquerda e as demais à direita. Sem ele, só a primeira
    coluna é texto.
    """
    if not linhas:
        return "_(sem linhas)_"

    esquerda = set(colunas_texto) if colunas_texto is not None else {colunas[0]}
    largura = {
        c: max(len(c), *(len(str(linha.get(c, ""))) for linha in linhas))
        for c in colunas
    }

    def alinhar(texto: str, coluna: str) -> str:
        return (
            texto.ljust(largura[coluna])
            if coluna in esquerda
            else texto.rjust(largura[coluna])
        )

    cabecalho = "| " + " | ".join(c.ljust(largura[c]) for c in colunas) + " |"
    separador = (
        "|"
        + "|".join(
            (":" + "-" * (largura[c] + 1))
            if c in esquerda
            else ("-" * (largura[c] + 1) + ":")
            for c in colunas
        )
        + "|"
    )
    corpo = [
        "| " + " | ".join(alinhar(str(linha.get(c, "")), c) for c in colunas) + " |"
        for linha in linhas
    ]
    return "\n".join([cabecalho, separador, *corpo])


def rodape_reproducao(comando: str, fontes: list[str]) -> str:
    """Rodapé com o comando que regenera a tabela e os arquivos de onde ela saiu."""
    lista = ", ".join(f"`{f}`" for f in fontes)
    return (
        f"> Tabela gerada por `{comando}` a partir de {lista}. "
        "Não editar à mão: cole novamente a saída do comando."
    )
