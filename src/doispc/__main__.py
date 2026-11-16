"""Ponto de entrada dos Nós do 2PC: escolhe o papel pelo ambiente e sobe o servidor.

O contêiner executa `python -m src.doispc`. A variável `DOISPC_PAPEL` seleciona
se este Nó é o Coordenador ou um Participante, de modo que a MESMA imagem sirva
os dois papéis: o algoritmo é escolhido pelo ambiente, nunca embutido no código.

Toda a configuração vem de variáveis de ambiente e é validada antes de abrir
socket ou tocar o disco: uma configuração inválida encerra
o processo sem efeito de rede ou de arquivo.

Variáveis comuns:

- `DOISPC_PAPEL`              "coordenador" ou "participante" (obrigatória).
- `SEMENTE`                  semente de aleatoriedade (default 0).
- `LOG_DIR`                  diretório do Log_Estruturado (default "results").

Coordenador:
- `COORD_ID`, `COORD_PORTA`  identidade e porta de escuta.
- `DOISPC_PARTICIPANTES`     "id@host:porta,..." dos Participantes.
- `DOISPC_DEADLINE_VOTO`     Deadline de coleta de Votos, em s (default 3.0).
- `DOISPC_DEADLINE_DECISAO`  Deadline de cada envio de rede, em s (default 2.0).
- `DOISPC_LOG_DURAVEL`       caminho do Log_Duravel (default /dados/coordenador.log).
- `DOISPC_CAIR_APOS_DECIDIR` "true" derruba o processo logo depois de gravar uma
                             Decisao. Só para os cenários `crash-coord` e `crash-coord-part`.

Participante:
- `PART_ID`, `PART_PORTA`    identidade e porta de escuta.
- `DOISPC_COORDENADORES`     "id@host:porta,..." dos Coordenadores a quem o Participante
                             responde. Sem ela, só o de `COORD_ID/HOST/PORTA`.
- `COORD_ID/HOST/PORTA`      identidade do Coordenador quando falta `DOISPC_COORDENADORES`.
- `DOISPC_VOTO`              "SIM" ou "NAO" (default "SIM"), a flag de voto.
- `DOISPC_DEADLINE`          Deadline de cada envio de rede, em s (default 2.0).
- `DOISPC_LOG_DURAVEL`       caminho do Log_Duravel (default /dados/participante-{id}.log).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.comum.config import ConfigInvalidaError, ConfigNo, ler_numero
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import LogEstruturado, abrir_log
from src.comum.modelos import No
from src.comum.rede import ClienteRede
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coordenador
from src.doispc.log_duravel import (
    FASE_DECISAO,
    VOTO_NAO,
    VOTO_SIM,
    EntradaLog,
    LogDuravelArquivo,
)
from src.doispc.participante import (
    ServicoParticipante,
    criar_app as criar_app_participante,
)


class _LogDuravelQueCai(LogDuravelArquivo):
    """Log_Duravel dos cenários com crash do Coordenador: o processo morre ao gravar uma Decisao.

    O `os._exit` vem depois do `fsync` e antes de a DECISAO entrar na fila de saída. Os
    Participantes que votaram SIM ficam em dúvida até o Coordenador reiniciar e se recuperar do
    log. Como a recuperação de uma transação em WAIT também grava uma Decisao, um contêiner com a
    flag ligada cai de novo nesse caso.
    """

    def __init__(self, caminho: Path | str, log: LogEstruturado) -> None:
        super().__init__(caminho)
        self._log = log

    def registrar(self, entrada: EntradaLog) -> None:
        super().registrar(entrada)
        if entrada.fase == FASE_DECISAO:
            self._log.evento("crash_apos_decidir", transacao=entrada.transacao, decisao=entrada.valor)
            os._exit(1)


def _no_de_env(prefixo: str, padrao_porta: int) -> No:
    return No(
        id=ler_numero(os.environ, f"{prefixo}_ID", int, None),
        host=os.environ.get(f"{prefixo}_HOST", "0.0.0.0"),
        porta=ler_numero(os.environ, f"{prefixo}_PORTA", int, str(padrao_porta)),
    )


def _ler_voto() -> str:
    """`DOISPC_VOTO` sem distinguir maiúsculas. Outro valor que não SIM nem NAO é recusado."""
    bruto = os.environ.get("DOISPC_VOTO", VOTO_SIM)
    voto = bruto.upper()
    if voto not in (VOTO_SIM, VOTO_NAO):
        raise ConfigInvalidaError("DOISPC_VOTO", bruto, f"{VOTO_SIM!r} ou {VOTO_NAO!r}")
    return voto


def _mapa_de_nos(variavel: str) -> dict[int, No]:
    """Lê "id@host:porta,..." da variável `variavel` num mapa id -> No."""
    bruto = os.environ.get(variavel, "")
    nos: dict[int, No] = {}
    for item in filter(None, (parte.strip() for parte in bruto.split(","))):
        ident, _, endereco = item.partition("@")
        host, _, porta = endereco.rpartition(":")
        if not (ident.isdigit() and host and porta.isdigit()):
            raise ConfigInvalidaError(variavel, item, "id@host:porta")
        nos[int(ident)] = No(id=int(ident), host=host, porta=int(porta))
    if not nos:
        raise ConfigInvalidaError(variavel, bruto, "id@host:porta separados por vírgula")
    return nos


def _abrir_log_e_controlador(
    papel: str, no_id: int, semente: int, cenario: dict
) -> tuple[object, ControladorFalhas]:
    """Abre o Log_Estruturado e cria o controlador de falhas deste Nó.

    O `inicio_replica` sai do controlador, com a Semente e o Plano_de_Falhas
    corrente. Os streams `drop` e `delay` do controlador derivam da Semente, e o
    Cenário de falha se repete.
    """
    log_dir = Path(os.environ.get("LOG_DIR", "results"))
    log = abrir_log(log_dir / f"doispc-{papel}-{no_id}.jsonl", no_id)
    controlador = ControladorFalhas(semente, log=log)
    registrar_plano_no_log(controlador, log, cenario=cenario)
    return log, controlador


@dataclass(frozen=True)
class AmbienteCoordenador:
    """Coordenador lido do ambiente e validado, sem arquivo aberto."""

    participantes: dict[int, No]
    semente: int
    deadline_voto: float
    deadline_decisao: float
    caminho_log: str
    cair_apos_decidir: bool

    def cenario(self) -> dict:
        """Os campos do Coordenador no `inicio_replica`. Quem chama acrescenta o algoritmo."""
        return {
            "participantes": sorted(self.participantes),
            "deadline_voto": self.deadline_voto,
            "deadline_decisao": self.deadline_decisao,
            "cair_apos_decidir": self.cair_apos_decidir,
        }


def ler_ambiente_coordenador(eu: No) -> AmbienteCoordenador:
    """Lê e valida as variáveis do Coordenador `eu`, sem efeito colateral."""
    participantes = _mapa_de_nos("DOISPC_PARTICIPANTES")
    semente = ler_numero(os.environ, "SEMENTE", int, "0")
    deadline_decisao = ler_numero(os.environ, "DOISPC_DEADLINE_DECISAO", float, "2.0")
    ConfigNo(
        id=eu.id,
        algoritmo="2pc",
        porta=eu.porta,
        semente=semente,
        deadline=deadline_decisao,
        sucessores=tuple(sorted(participantes)),
    ).validar()
    return AmbienteCoordenador(
        participantes=participantes,
        semente=semente,
        deadline_voto=ler_numero(os.environ, "DOISPC_DEADLINE_VOTO", float, "3.0"),
        deadline_decisao=deadline_decisao,
        caminho_log=os.environ.get("DOISPC_LOG_DURAVEL", "/dados/coordenador.log"),
        cair_apos_decidir=os.environ.get("DOISPC_CAIR_APOS_DECIDIR", "").strip().lower()
        in ("1", "true", "sim", "yes"),
    )


def montar_coordenador(
    eu: No, amb: AmbienteCoordenador, *, log: LogEstruturado, cliente: ClienteRede
) -> ServicoCoordenador:
    """Abre o Log_Duravel, cria o serviço e o recupera do log antes de servir."""
    log_duravel = (
        _LogDuravelQueCai(amb.caminho_log, log)
        if amb.cair_apos_decidir
        else LogDuravelArquivo(amb.caminho_log)
    )
    servico = ServicoCoordenador(
        eu,
        amb.participantes,
        log_duravel=log_duravel,
        cliente=cliente,
        deadline_voto=amb.deadline_voto,
        deadline_decisao=amb.deadline_decisao,
        log=log,
    )
    servico.recuperar()
    return servico


def construir_coordenador() -> tuple[object, int]:
    """Monta o Coordenador a partir do ambiente, validando antes de qualquer efeito."""
    eu = _no_de_env("COORD", 8000)
    amb = ler_ambiente_coordenador(eu)
    log, controlador = _abrir_log_e_controlador(
        "coordenador",
        eu.id,
        amb.semente,
        {"algoritmo": "2pc", "papel": "coordenador", **amb.cenario()},
    )
    servico = montar_coordenador(
        eu, amb, log=log, cliente=ClienteComFalhas(ClienteRede(), controlador)
    )
    return criar_app_coordenador(servico, controlador=controlador), eu.porta


def construir_participante() -> tuple[object, int]:
    """Monta um Participante a partir do ambiente, validando antes de qualquer efeito."""
    eu = _no_de_env("PART", 8100)
    if "DOISPC_COORDENADORES" in os.environ:
        coordenadores = _mapa_de_nos("DOISPC_COORDENADORES")
    else:
        coordenador = _no_de_env("COORD", 8000)
        coordenadores = {coordenador.id: coordenador}
    semente = ler_numero(os.environ, "SEMENTE", int, "0")
    deadline = ler_numero(os.environ, "DOISPC_DEADLINE", float, "2.0")
    voto = _ler_voto()

    config = ConfigNo(
        id=eu.id,
        algoritmo="2pc",
        porta=eu.porta,
        semente=semente,
        deadline=deadline,
        sucessores=tuple(sorted(coordenadores)),
    )
    config.validar()

    caminho_log = os.environ.get(
        "DOISPC_LOG_DURAVEL", f"/dados/participante-{eu.id}.log"
    )
    log, controlador = _abrir_log_e_controlador(
        "participante",
        eu.id,
        semente,
        {
            "algoritmo": "2pc",
            "papel": "participante",
            "coordenadores": sorted(coordenadores),
            "voto_desejado": voto,
            "deadline": deadline,
        },
    )
    servico = ServicoParticipante(
        eu,
        coordenadores,
        log_duravel=LogDuravelArquivo(caminho_log),
        cliente=ClienteComFalhas(ClienteRede(), controlador),
        deadline=deadline,
        voto_desejado=voto,
        log=log,
    )
    # Recupera eventual Decisao de uma transação anterior gravada no volume:
    # relê o Log_Duravel de todas as transações conhecidas.
    for tx in servico._log_duravel.transacoes():  # noqa: SLF001 - adaptador do próprio pacote
        servico.recuperar(tx)
    return criar_app_participante(servico, controlador=controlador), eu.porta


def main() -> None:
    """Valida a configuração do ambiente e serve o Nó com uvicorn."""
    import uvicorn

    papel = os.environ.get("DOISPC_PAPEL", "").strip().lower()
    try:
        if papel == "coordenador":
            app, porta = construir_coordenador()
        elif papel == "participante":
            app, porta = construir_participante()
        else:
            raise ConfigInvalidaError(
                "DOISPC_PAPEL", papel, "'coordenador' ou 'participante'"
            )
    except ConfigInvalidaError as erro:
        raise SystemExit(f"configuração inválida do Nó do 2PC: {erro}") from erro

    print(f"[2pc] {papel} escutando em 0.0.0.0:{porta}")
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
