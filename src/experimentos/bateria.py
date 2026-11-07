"""Planejamento e execução da bateria de Réplicas.

Uma bateria executa várias Réplicas com Sementes independentes, variando UM
parâmetro em pelo menos três níveis, e grava um CSV com uma linha por Réplica
(Requisitos 10.1, 10.4). Cada linha carrega a Semente e o ambiente de execução
(Requisitos 10.6, 13.6). A bateria inteira é validada ANTES de
sobrescrever qualquer arquivo de resultado (Requisito 10.5), e uma Réplica que
falha é registrada sem abortar o restante (Requisito 10.7).

A execução por Réplica é **plugável**: o planejador produz `ItemReplica`s com os
parâmetros, e um `Runner` injetável os transforma em métricas. Em produção o
runner dirige a Máquina_de_Estados pura em processo (`runner_anel`,
`runner_doispc`), o que torna a bateria determinística e testável sem Docker; um
runner fake pode ser injetado nos testes para checar o esquema do CSV e a
reprodução por Semente sem rodar algoritmo nenhum.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.experimentos.ambiente import coletar_ambiente

# Uma Réplica planejada: os parâmetros que a identificam. `parametros` é o mapa
# plano que vira colunas no CSV; `semente` é a Semente independente da Réplica.
@dataclass(frozen=True)
class ItemReplica:
    """Uma Réplica planejada, com sua Semente e os parâmetros que a definem.

    `indice` é a posição global da Réplica na bateria (0-based), útil para
    reproduzir a ordem. `nivel` é o valor do parâmetro variado nesta Réplica, e
    `k` a repetição dentro daquele nível. `parametros` são os campos que o runner
    consome e que também viram colunas do CSV.
    """

    indice: int
    nivel: Any
    k: int
    semente: int
    parametros: Mapping[str, Any] = field(default_factory=dict)

    @property
    def descricao(self) -> str:
        """Rótulo legível da Réplica para o progresso no console."""
        return f"nivel={self.nivel} k={self.k} semente={self.semente}"


# Um Runner recebe o `ItemReplica` e devolve o mapa plano de métricas da Réplica.
# As chaves do mapa serão colunas do CSV. Levantar exceção sinaliza falha de
# Réplica, que a bateria registra e segue (Requisito 10.7).
Runner = Callable[[ItemReplica], Mapping[str, Any]]


def planejar_bateria(
    *,
    parametro: str,
    niveis: Sequence[Any],
    replicas: int,
    semente_base: int,
    fixos: Mapping[str, Any] | None = None,
) -> list[ItemReplica]:
    """Planeja as Réplicas variando `parametro` em `niveis`, com `replicas` cada.

    Exige pelo menos três níveis (Requisito 10.1). As Sementes são independentes
    e derivadas de `semente_base` pela posição global da Réplica, de modo que a
    mesma `semente_base` reproduza exatamente o mesmo conjunto de Réplicas
    (Requisito 10.4). `fixos` são parâmetros constantes acrescentados a toda
    Réplica (por exemplo, a taxa de falhas quando o parâmetro variado é o número
    de Nós).
    """
    if len(niveis) < 3:
        raise ValueError(
            f"a bateria deve variar o parâmetro em >= 3 níveis; recebido {len(niveis)}"
        )
    if replicas < 1:
        raise ValueError(f"replicas deve ser >= 1; recebido {replicas}")
    if not (0 <= semente_base <= 2_147_483_647):
        raise ValueError(
            f"semente_base deve ser inteiro em [0, 2147483647]; recebido {semente_base}"
        )
    fixos = dict(fixos or {})

    itens: list[ItemReplica] = []
    indice = 0
    for nivel in niveis:
        for k in range(replicas):
            semente = semente_base + indice
            parametros = {**fixos, parametro: nivel}
            itens.append(
                ItemReplica(
                    indice=indice,
                    nivel=nivel,
                    k=k,
                    semente=semente,
                    parametros=parametros,
                )
            )
            indice += 1
    return itens


@dataclass(frozen=True)
class ResultadoBateria:
    """Desfecho de uma bateria: linhas gravadas, falhas e código de saída."""

    linhas: list[dict[str, Any]]
    falhas: list[tuple[ItemReplica, str]]
    codigo_saida: int


def executar_bateria(
    itens: Sequence[ItemReplica],
    runner: Runner,
    destino: Path,
    *,
    colunas_meta: Sequence[str] = ("algoritmo", "parametro_variado"),
    meta: Mapping[str, Any] | None = None,
    saida=sys.stdout,
    erro=sys.stderr,
) -> ResultadoBateria:
    """Executa as Réplicas em ordem e grava um CSV com uma linha por Réplica.

    A bateria é validada ANTES de tocar o arquivo
    de saída (aqui, validando que há itens e que o planejamento é consistente),
    de modo que um erro de configuração não destrua resultados anteriores
    (Requisito 10.5). Cada linha carrega a Semente e o ambiente (Requisitos 10.6,
    13.6). Uma Réplica que levanta exceção é registrada e a bateria segue
    (Requisito 10.7); o código de saída é 1 se houve qualquer falha, senão 0.

    O CSV só é aberto para escrita DEPOIS de todas as Réplicas rodarem, para que
    uma falha no meio não deixe o arquivo pela metade nem sobrescreva um resultado
    bom com um parcial — a validação-antes-de-sobrescrever levada ao fim.
    """
    if not itens:
        raise ValueError("a bateria não tem Réplicas a executar")

    ambiente = coletar_ambiente().como_colunas()
    meta = dict(meta or {})
    total = len(itens)
    linhas: list[dict[str, Any]] = []
    falhas: list[tuple[ItemReplica, str]] = []

    for posicao, item in enumerate(itens, start=1):
        print(f"[{posicao}/{total}] {item.descricao}", file=saida)
        try:
            metricas = runner(item)
        except Exception as exc:  # noqa: BLE001 - qualquer falha de Réplica
            motivo = f"{type(exc).__name__}: {exc}"
            falhas.append((item, motivo))
            print(f"[erro] falha na réplica {item.descricao}: {motivo}", file=erro)
            continue
        linha: dict[str, Any] = {}
        for coluna in colunas_meta:
            linha[coluna] = meta.get(coluna, "")
        linha["indice"] = item.indice
        linha["nivel"] = item.nivel
        linha["k"] = item.k
        linha["semente"] = item.semente
        for chave, valor in item.parametros.items():
            linha[chave] = valor
        for chave, valor in metricas.items():
            linha[chave] = valor
        linha.update(ambiente)
        linhas.append(linha)

    _gravar_csv(destino, linhas, erro=erro)

    for item, motivo in falhas:
        print(f"[erro] réplica falha: {item.descricao}: {motivo}", file=erro)
    print(
        f"[bateria] total={total} sucessos={len(linhas)} falhas={len(falhas)} "
        f"-> {destino}",
        file=saida,
    )
    codigo = 1 if falhas else 0
    return ResultadoBateria(linhas=linhas, falhas=falhas, codigo_saida=codigo)


def _gravar_csv(destino: Path, linhas: Sequence[Mapping[str, Any]], *, erro) -> None:
    """Grava `linhas` num CSV, com o cabeçalho na ordem da primeira linha.

    Avisa em stderr se o arquivo já existia (foi sobrescrito). Se não há linhas (todas as Réplicas falharam),
    grava um arquivo vazio para não deixar um resultado anterior enganosamente
    válido no lugar.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        print(
            f"[aviso] '{destino}' já existia e foi sobrescrito ao fim da bateria.",
            file=erro,
        )
    if not linhas:
        destino.write_text("", encoding="utf-8")
        return
    colunas = list(linhas[0].keys())
    with destino.open("w", encoding="utf-8", newline="") as fluxo:
        escritor = csv.DictWriter(fluxo, fieldnames=colunas)
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow(linha)
