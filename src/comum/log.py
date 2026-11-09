"""Log estruturado: uma linha JSON por evento, consumido pela análise e invariantes.

Cada evento vira uma linha JSON com, no mínimo, o timestamp físico (epoch em
segundos), o id do Nó e o tipo do evento, mais os campos específicos do evento.
A análise e os invariantes consomem esses arquivos sob
`results/` sem acesso ao estado em memória dos Nós.

Uma falha ao escrever uma linha não deve alterar as decisões do algoritmo: o
Nó registra o problema e prossegue. O log é, portanto,
observação, nunca parte do caminho de decisão.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, TextIO


class LogEstruturado:
    """Escreve eventos como linhas JSON, carimbando timestamp, id do Nó e tipo.

    Recebe um fluxo de texto já aberto (um arquivo sob `results/` em produção,
    um buffer em memória nos testes). Não abre nem fecha o fluxo: quem o cria é
    responsável por isso, o que mantém o log testável sem tocar o disco.
    """

    def __init__(self, no_id: int, destino: TextIO, *, relogio=time.time) -> None:
        self._no_id = no_id
        self._destino = destino
        self._relogio = relogio

    def evento(self, tipo: str, **campos: Any) -> None:
        """Registra um evento `tipo` com `campos` arbitrários.

        Em caso de falha de escrita, emite um aviso em stderr e segue, sem
        propagar a exceção para o algoritmo.
        """
        linha = _montar_linha(self._relogio(), self._no_id, tipo, campos)
        try:
            self._destino.write(linha + "\n")
            self._destino.flush()
        except OSError as erro:  # pragma: no cover - caminho de degradação
            print(
                f"[log] falha ao gravar evento {tipo!r} do nó {self._no_id}: {erro}",
                file=sys.stderr,
            )

    def inicio_replica(
        self, *, semente: int, plano_de_falhas: Mapping[str, Any], cenario: Mapping[str, Any]
    ) -> None:
        """Registra, no início da Réplica, a Semente, o Plano_de_Falhas e o Cenário.

        É o que torna a Réplica reproduzível a partir do próprio log.
        """
        self.evento(
            "inicio_replica",
            semente=semente,
            plano_de_falhas=dict(plano_de_falhas),
            cenario=dict(cenario),
        )


def _montar_linha(agora: float, no_id: int, tipo: str, campos: Mapping[str, Any]) -> str:
    """Monta a linha JSON de um evento com as chaves reservadas primeiro.

    `ts`, `no` e `tipo` são as chaves reservadas; os campos do evento não podem
    sobrescrevê-las, para que a análise sempre as encontre.
    """
    registro: dict[str, Any] = {"ts": agora, "no": no_id, "tipo": tipo}
    for chave, valor in campos.items():
        if chave in registro:
            continue
        registro[chave] = valor
    return json.dumps(registro, ensure_ascii=False, sort_keys=False)


def abrir_log(caminho: Path, no_id: int, *, relogio=time.time) -> LogEstruturado:
    """Abre (criando diretórios) um arquivo de log sob `results/` e devolve o logger.

    O chamador é dono do fluxo subjacente via `LogEstruturado`; em produção o
    processo do Nó o mantém aberto durante toda a Réplica.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fluxo = caminho.open("a", encoding="utf-8")
    return LogEstruturado(no_id, fluxo, relogio=relogio)
