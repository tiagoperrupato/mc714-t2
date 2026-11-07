"""Log_Duravel write-ahead do 2PC, atrás de uma interface que isola o IO de disco.

A Máquina_de_Estados do 2PC é pura (Requisito 6.1): ela não sabe se o
Log_Duravel vive em memória ou em arquivo. Este módulo define essa fronteira.

`LogDuravel` é o contrato (um `Protocol`): anexar uma `EntradaLog` em write-ahead
e reconstruir o estado persistido relendo as entradas. Duas implementações o
satisfazem:

- `LogDuravelMemoria`: guarda as entradas numa lista. Serve aos testes e às
  demonstrações em memória, exercitando a mesma ordem write-ahead sem tocar o
  disco.
- `LogDuravelArquivo`: anexa uma linha JSON por entrada e faz `flush`/`fsync`
  antes de retornar, de modo que a entrada sobreviva a um crash do processo. A
  recuperação relê o arquivo inteiro e devolve o mesmo estado de antes do
  reinício (Requisito 6.6).

A ordem write-ahead é a regra central (Requisito 6.4, 6.5): o adaptador de rede
grava o Voto **antes** de respondê-lo e a Decisao **antes** de aplicá-la. Como
`registrar` só retorna depois de a entrada estar durável, chamar `registrar`
antes de responder pela rede é o que garante a durabilidade. Uma vez anexada,
uma Decisao nunca é reescrita nem removida, o que sustenta a não-reversão
(Requisito 6.8): a recuperação sempre relê a primeira Decisao registrada.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol, runtime_checkable

# Fases persistidas no Log_Duravel. VOTO registra a decisão local de um
# Participante ao PREPARE; DECISAO registra o resultado da transação (COMMIT ou
# ABORT), tanto no Coordenador quanto no Participante.
FASE_VOTO = "voto"
FASE_DECISAO = "decisao"

# Valores de Voto e de Decisao, mantidos como constantes para evitar strings
# soltas espalhadas pelo código.
VOTO_SIM = "SIM"
VOTO_NAO = "NAO"
DECISAO_COMMIT = "COMMIT"
DECISAO_ABORT = "ABORT"


@dataclass(frozen=True)
class EntradaLog:
    """Uma entrada imutável do Log_Duravel, ligada a uma transação.

    `transacao` identifica a transação 2PC. `fase` é `FASE_VOTO` ou
    `FASE_DECISAO`. `valor` é o Voto (`SIM`/`NAO`) ou a Decisao
    (`COMMIT`/`ABORT`). `no` é o id do Nó que gravou a entrada, útil para
    auditoria do log e para a análise.
    """

    transacao: str
    fase: str
    valor: str
    no: int

    def para_json(self) -> dict[str, Any]:
        """Dicionário serializável desta entrada (uma linha do arquivo de log)."""
        return {
            "transacao": self.transacao,
            "fase": self.fase,
            "valor": self.valor,
            "no": self.no,
        }

    @classmethod
    def de_json(cls, dados: Mapping[str, Any]) -> "EntradaLog":
        """Reconstrói uma `EntradaLog` a partir de uma linha desserializada."""
        return cls(
            transacao=dados["transacao"],
            fase=dados["fase"],
            valor=dados["valor"],
            no=dados["no"],
        )


@dataclass(frozen=True)
class EstadoPersistido:
    """Resumo do que uma transação deixou gravado no Log_Duravel.

    `voto` é o último Voto registrado para a transação, ou `None` se o Nó nunca
    votou. `decisao` é a Decisao registrada, ou `None` se ainda não há Decisao
    durável — o caso do Participante em Estado_Em_Duvida, que votou mas não
    recebeu a Decisao antes de um crash do Coordenador.
    """

    transacao: str
    voto: Optional[str] = None
    decisao: Optional[str] = None


@runtime_checkable
class LogDuravel(Protocol):
    """Contrato do Log_Duravel: anexar em write-ahead e recuperar o estado.

    A Máquina_de_Estados depende apenas deste contrato, nunca de uma
    implementação concreta (Requisito 6.1). `registrar` só retorna depois de a
    entrada estar durável; é por isso que gravar antes de responder pela rede
    garante a durabilidade (Requisito 6.4).
    """

    def registrar(self, entrada: EntradaLog) -> None:
        """Anexa `entrada` em write-ahead; retorna só quando ela está durável."""
        ...

    def recuperar(self, transacao: str) -> EstadoPersistido:
        """Relê o log e devolve o Voto e a Decisao persistidos de `transacao`."""
        ...

    def transacoes(self) -> tuple[str, ...]:
        """Ids de transação com ao menos uma entrada, na ordem de primeiro registro."""
        ...


def _reduzir(entradas: Iterable[EntradaLog], transacao: str) -> EstadoPersistido:
    """Reconstrói o `EstadoPersistido` de uma transação relendo suas entradas.

    O Voto segue a última entrada de Voto. A Decisao segue a **primeira**
    Decisao registrada e ignora qualquer entrada posterior: uma vez decidida,
    uma transação não reverte (Requisito 6.8), então a recuperação sempre
    reporta a mesma Decisao de antes do reinício (Requisito 6.6).
    """
    voto: Optional[str] = None
    decisao: Optional[str] = None
    for entrada in entradas:
        if entrada.transacao != transacao:
            continue
        if entrada.fase == FASE_VOTO:
            voto = entrada.valor
        elif entrada.fase == FASE_DECISAO and decisao is None:
            decisao = entrada.valor
    return EstadoPersistido(transacao=transacao, voto=voto, decisao=decisao)


def _ordem_transacoes(entradas: Iterable[EntradaLog]) -> tuple[str, ...]:
    """Ids de transação na ordem em que apareceram pela primeira vez no log."""
    vistos: list[str] = []
    marcados: set[str] = set()
    for entrada in entradas:
        if entrada.transacao not in marcados:
            marcados.add(entrada.transacao)
            vistos.append(entrada.transacao)
    return tuple(vistos)


class LogDuravelMemoria:
    """Log_Duravel em memória: mesma semântica write-ahead, sem tocar o disco.

    Útil para testes e para as demonstrações em memória da tarefa 5. As entradas
    ficam numa lista, na ordem de registro; `recuperar` as reduz como a
    implementação em arquivo faria após reler o arquivo.
    """

    def __init__(self) -> None:
        self._entradas: list[EntradaLog] = []

    def registrar(self, entrada: EntradaLog) -> None:
        """Anexa a entrada à lista (em memória já é durável para o teste)."""
        self._entradas.append(entrada)

    def recuperar(self, transacao: str) -> EstadoPersistido:
        """Reduz as entradas em memória ao estado persistido da transação."""
        return _reduzir(self._entradas, transacao)

    def transacoes(self) -> tuple[str, ...]:
        """Transações registradas, na ordem de primeiro aparecimento."""
        return _ordem_transacoes(self._entradas)

    @property
    def entradas(self) -> tuple[EntradaLog, ...]:
        """View somente leitura das entradas, para inspeção em testes e demos."""
        return tuple(self._entradas)


class LogDuravelArquivo:
    """Log_Duravel em arquivo: uma linha JSON por entrada, com `fsync` write-ahead.

    `registrar` anexa a linha, dá `flush` no buffer de Python e `fsync` no
    descritor, só então retornando — é isso que torna a entrada durável antes de
    o adaptador responder pela rede (Requisito 6.4, 6.5). `recuperar` abre o
    arquivo, relê todas as linhas e reduz ao estado persistido, de modo que um
    Nó reiniciado reporte a mesma Decisao de antes (Requisito 6.6).

    Linhas corrompidas (por exemplo uma escrita parcial interrompida por um
    crash no meio do append) são ignoradas na recuperação: uma entrada só conta
    quando sua linha JSON está completa, o que preserva a semântica write-ahead.
    """

    def __init__(self, caminho: Path | str) -> None:
        self._caminho = Path(caminho)
        self._caminho.parent.mkdir(parents=True, exist_ok=True)

    def registrar(self, entrada: EntradaLog) -> None:
        """Anexa a entrada e força sua gravação em disco antes de retornar.

        Se o arquivo não termina em quebra de linha, há um registro torto de uma
        escrita interrompida por crash: fecha-se esse registro com um `\\n` antes
        de anexar, de modo que a nova entrada comece numa linha própria e não se
        funda à linha truncada. A linha torta vira uma linha corrompida isolada,
        que a recuperação ignora, preservando a semântica write-ahead.
        """
        linha = json.dumps(entrada.para_json(), ensure_ascii=False, sort_keys=True)
        with self._caminho.open("a", encoding="utf-8") as fluxo:
            if self._termina_sem_quebra():
                fluxo.write("\n")
            fluxo.write(linha + "\n")
            fluxo.flush()
            os.fsync(fluxo.fileno())

    def _termina_sem_quebra(self) -> bool:
        """Verdadeiro se o arquivo existe, não está vazio e não termina em `\\n`."""
        if not self._caminho.exists():
            return False
        tamanho = self._caminho.stat().st_size
        if tamanho == 0:
            return False
        with self._caminho.open("rb") as bruto:
            bruto.seek(-1, os.SEEK_END)
            return bruto.read(1) != b"\n"

    def _ler(self) -> list[EntradaLog]:
        """Lê o arquivo inteiro, ignorando linhas vazias ou corrompidas."""
        if not self._caminho.exists():
            return []
        entradas: list[EntradaLog] = []
        with self._caminho.open("r", encoding="utf-8") as fluxo:
            for linha in fluxo:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    dados = json.loads(linha)
                    entradas.append(EntradaLog.de_json(dados))
                except (json.JSONDecodeError, KeyError):
                    # Escrita parcial interrompida por crash: ignora, pois a
                    # entrada não chegou a ficar durável.
                    continue
        return entradas

    def recuperar(self, transacao: str) -> EstadoPersistido:
        """Relê o arquivo e reduz ao estado persistido da transação."""
        return _reduzir(self._ler(), transacao)

    def transacoes(self) -> tuple[str, ...]:
        """Transações presentes no arquivo, na ordem de primeiro aparecimento."""
        return _ordem_transacoes(self._ler())
