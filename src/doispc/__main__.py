"""Ponto de entrada dos Nós do 2PC: escolhe o papel pelo ambiente e sobe o servidor.

O contêiner executa `python -m src.doispc`. A variável `DOISPC_PAPEL` seleciona
se este Nó é o Coordenador ou um Participante, de modo que a MESMA imagem sirva
os dois papéis — o algoritmo é escolhido pelo ambiente, nunca embutido no código.

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
                             Decisao. Só para o cenário `crash-coord`.

Participante:
- `PART_ID`, `PART_PORTA`    identidade e porta de escuta.
- `COORD_ID/HOST/PORTA`      identidade do Coordenador.
- `DOISPC_VOTO`              "SIM" ou "NAO" — a flag de voto.
- `DOISPC_DEADLINE`          Deadline de cada envio de rede, em s (default 2.0).
- `DOISPC_LOG_DURAVEL`       caminho do Log_Duravel (default /dados/participante-{id}.log).
"""

from __future__ import annotations

import os
from pathlib import Path

from src.comum.config import ConfigInvalidaError, ConfigNo
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import LogEstruturado, abrir_log
from src.comum.modelos import No
from src.comum.rede import ClienteRede
from src.doispc.coordenador import (
    ServicoCoordenador,
    _participantes_de_env,
    criar_app as criar_app_coordenador,
)
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
    """Log_Duravel do cenário `crash-coord`: o processo morre logo depois de gravar uma Decisao.

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
        id=int(os.environ[f"{prefixo}_ID"]),
        host=os.environ.get(f"{prefixo}_HOST", "0.0.0.0"),
        porta=int(os.environ.get(f"{prefixo}_PORTA", str(padrao_porta))),
    )


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


def construir_coordenador() -> tuple[object, int]:
    """Monta o Coordenador a partir do ambiente, validando antes de qualquer efeito."""
    eu = _no_de_env("COORD", 8000)
    participantes = _participantes_de_env()
    semente = int(os.environ.get("SEMENTE", "0"))
    deadline_voto = float(os.environ.get("DOISPC_DEADLINE_VOTO", "3.0"))
    deadline_decisao = float(os.environ.get("DOISPC_DEADLINE_DECISAO", "2.0"))

    config = ConfigNo(
        id=eu.id,
        algoritmo="2pc",
        porta=eu.porta,
        semente=semente,
        deadline=deadline_decisao,
        sucessores=tuple(sorted(participantes)),
    )
    config.validar()

    cair_apos_decidir = os.environ.get("DOISPC_CAIR_APOS_DECIDIR", "").strip().lower() in (
        "1", "true", "sim", "yes",
    )

    caminho_log = os.environ.get("DOISPC_LOG_DURAVEL", "/dados/coordenador.log")
    log, controlador = _abrir_log_e_controlador(
        "coordenador",
        eu.id,
        semente,
        {
            "algoritmo": "2pc",
            "papel": "coordenador",
            "participantes": sorted(participantes),
            "deadline_voto": deadline_voto,
            "deadline_decisao": deadline_decisao,
            "cair_apos_decidir": cair_apos_decidir,
        },
    )
    log_duravel = (
        _LogDuravelQueCai(caminho_log, log) if cair_apos_decidir else LogDuravelArquivo(caminho_log)
    )
    servico = ServicoCoordenador(
        eu,
        participantes,
        log_duravel=log_duravel,
        cliente=ClienteComFalhas(ClienteRede(), controlador),
        deadline_voto=deadline_voto,
        deadline_decisao=deadline_decisao,
        log=log,
    )
    servico.recuperar()
    return criar_app_coordenador(servico, controlador=controlador), eu.porta


def construir_participante() -> tuple[object, int]:
    """Monta um Participante a partir do ambiente, validando antes de qualquer efeito."""
    eu = _no_de_env("PART", 8100)
    coordenador = _no_de_env("COORD", 8000)
    semente = int(os.environ.get("SEMENTE", "0"))
    deadline = float(os.environ.get("DOISPC_DEADLINE", "2.0"))
    voto = VOTO_NAO if os.environ.get("DOISPC_VOTO", "SIM").upper() == VOTO_NAO else VOTO_SIM

    config = ConfigNo(
        id=eu.id,
        algoritmo="2pc",
        porta=eu.porta,
        semente=semente,
        deadline=deadline,
        sucessores=(coordenador.id,),
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
            "coordenador": coordenador.id,
            "voto_desejado": voto,
            "deadline": deadline,
        },
    )
    servico = ServicoParticipante(
        eu,
        coordenador,
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
    except (KeyError, ValueError, ConfigInvalidaError) as erro:
        raise SystemExit(f"configuração inválida do Nó do 2PC: {erro}") from erro

    print(f"[2pc] {papel} escutando em 0.0.0.0:{porta}")
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
