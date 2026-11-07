"""App FastAPI do Participante do 2PC — adaptador fino sobre a Máquina_de_Estados.

Este módulo **não reimplementa 2PC**. Ele é o adaptador de rede (Requisito 7.1)
que liga a Máquina_de_Estados pura de `src.doispc.maquina` ao Transporte com
Deadline de `src.comum.rede` e ao `Log_Duravel` de `src.doispc.log_duravel`.
O fluxo é sempre o mesmo:

1. recebe uma Mensagem (PREPARE ou DECISAO) por `POST /mensagem`;
2. entrega a Mensagem a `participante_recebe`, que devolve o novo estado e uma
   lista ordenada de `Acao`;
3. executa as `Acao` **na ordem em que vêm**, e nessa ordem está codificada a
   regra write-ahead: uma `GravarLog` sempre precede o `Aplicar`/`Enviar` que
   dependem dela (Requisito 6.4, 6.5). O adaptador não precisa lembrar a regra;
   basta obedecer à sequência.

O Participante pode ser configurado para votar NAO (Requisito 7.4): o
`voto_desejado` é fixado na construção a partir de uma flag (env `DOISPC_VOTO`),
e repassado à Máquina_de_Estados a cada PREPARE. Todo o resto — atomicidade,
não-reversão, recuperação — já está na lógica pura e é apenas exercido aqui.

A recuperação após reinício (Requisito 6.6) relê o Log_Duravel na partida e
reconstrói o estado com `recuperar_participante`, de modo que um Participante
reiniciado reporte a mesma Decisao de antes.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede
from src.doispc.executor import ExecutorAcoes
from src.doispc.log_duravel import (
    VOTO_NAO,
    VOTO_SIM,
    LogDuravel,
    LogDuravelArquivo,
)
from src.doispc.maquina import (
    EstadoParticipante,
    participante_recebe,
    recuperar_participante,
)


class ServicoParticipante:
    """Núcleo do Participante, independente de FastAPI, para ser testável sem servidor.

    Mantém o estado da transação corrente (um Participante trata uma transação
    por vez neste projeto), o `voto_desejado` configurado e as dependências de
    efeito colateral (Log_Duravel e Transporte), delegando toda a decisão à
    Máquina_de_Estados.
    """

    def __init__(
        self,
        participante: No,
        coordenador: No,
        *,
        log_duravel: LogDuravel,
        cliente: ClienteRede,
        deadline: float,
        voto_desejado: str = VOTO_SIM,
        log: Optional[LogEstruturado] = None,
    ) -> None:
        self._eu = participante
        self._coordenador = coordenador
        self._voto_desejado = VOTO_SIM if voto_desejado == VOTO_SIM else VOTO_NAO
        self._log_duravel = log_duravel
        self._log = log
        self._estado: Optional[EstadoParticipante] = None
        # O Participante só fala com o Coordenador; o executor resolve o id do
        # destino para a URL do Coordenador.
        self._executor = ExecutorAcoes(
            no_id=participante.id,
            log_duravel=log_duravel,
            cliente=cliente,
            deadline=deadline,
            enderecos={coordenador.id: coordenador.base_url},
            log=log,
        )

    @property
    def estado(self) -> Optional[EstadoParticipante]:
        """Estado corrente do Participante (ou `None` antes do primeiro PREPARE)."""
        return self._estado

    def recuperar(self, transacao: str) -> Optional[EstadoParticipante]:
        """Reconstrói o estado a partir do Log_Duravel após reinício (Requisito 6.6)."""
        persistido = self._log_duravel.recuperar(transacao)
        if persistido.voto is None and persistido.decisao is None:
            return None
        self._estado = recuperar_participante(persistido)
        if self._log is not None:
            self._log.evento(
                "recuperacao",
                transacao=transacao,
                voto=self._estado.voto,
                decisao=self._estado.decisao,
                em_duvida=self._estado.em_duvida,
            )
        return self._estado

    def receber(self, mensagem: Mensagem) -> Mapping[str, Any]:
        """Entrega a Mensagem à Máquina_de_Estados e executa as `Acao` devolvidas.

        A resposta HTTP é um ACK imediato de recebimento; ela não espera rodadas
        subsequentes (Requisito 3.5). As Mensagens de saída (o VOTO, o ACK da
        DECISAO) são despachadas pelo executor, por fora da resposta.
        """
        if self._log is not None:
            self._log.evento(
                "recebe",
                msg_tipo=mensagem.tipo,
                origem=mensagem.origem,
                transacao=mensagem.corpo.get("transacao"),
            )
        novo, acoes = participante_recebe(
            self._estado,
            self._eu.id,
            self._coordenador.id,
            mensagem,
            voto_desejado=self._voto_desejado,
        )
        self._estado = novo
        self._executor.executar(acoes)
        return {"ack": True}

    def instantaneo(self) -> dict[str, Any]:
        """Resumo serializável do estado corrente, para o endpoint `/estado`."""
        estado = self._estado
        return {
            "no": self._eu.id,
            "voto_desejado": self._voto_desejado,
            "transacao": estado.transacao if estado else None,
            "voto": estado.voto if estado else None,
            "decisao": estado.decisao if estado else None,
            "aplicada": estado.aplicada if estado else False,
            "em_duvida": estado.em_duvida if estado else False,
        }


def _no_de_env(prefixo: str, padrao_porta: int) -> No:
    """Lê a identidade de um Nó (id/host/porta) das variáveis de ambiente."""
    return No(
        id=int(os.environ[f"{prefixo}_ID"]),
        host=os.environ.get(f"{prefixo}_HOST", "0.0.0.0"),
        porta=int(os.environ.get(f"{prefixo}_PORTA", str(padrao_porta))),
    )


def criar_servico_de_env() -> ServicoParticipante:
    """Monta um `ServicoParticipante` a partir do ambiente (usado pelo contêiner).

    Variáveis esperadas: `PART_ID`, `PART_PORTA`, `COORD_ID`, `COORD_HOST`,
    `COORD_PORTA`, `DOISPC_VOTO` (SIM/NAO), `DOISPC_DEADLINE`,
    `DOISPC_LOG_DURAVEL` (caminho do arquivo), `DOISPC_RESULTS` (dir de log).
    O voto NAO é a flag do Requisito 7.4.
    """
    eu = _no_de_env("PART", 8100)
    coordenador = _no_de_env("COORD", 8000)
    voto = VOTO_NAO if os.environ.get("DOISPC_VOTO", "SIM").upper() == VOTO_NAO else VOTO_SIM
    deadline = float(os.environ.get("DOISPC_DEADLINE", "2.0"))
    caminho_log = os.environ.get(
        "DOISPC_LOG_DURAVEL", f"/dados/participante-{eu.id}.log"
    )
    log_duravel = LogDuravelArquivo(caminho_log)
    cliente = ClienteRede()
    return ServicoParticipante(
        eu,
        coordenador,
        log_duravel=log_duravel,
        cliente=cliente,
        deadline=deadline,
        voto_desejado=voto,
    )


def criar_app(servico: Optional[ServicoParticipante] = None, *, controlador=None):
    """Cria o app FastAPI do Participante em volta de um `ServicoParticipante`.

    Importa FastAPI localmente, mantendo a fronteira: importar este módulo não
    exige FastAPI para quem só quer o `ServicoParticipante` (por exemplo, testes
    de integração que falam direto com o serviço).

    Com um `controlador` de falhas, o recebimento de PREPARE/DECISAO passa pelo
    controlador antes da Máquina (drop/delay/pause/crash), os endpoints
    `/falha/*` são expostos (Requisito 8.3) e um crash do Participante devolve
    503. Um crash do Coordenador no meio da coleta deixa este Participante em
    Estado_Em_Duvida (Requisito 8.5), que já é comportamento da Máquina pura.
    """
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    from src.comum.rede import CAMINHO_MENSAGEM

    if servico is None:
        servico = criar_servico_de_env()

    app = FastAPI()

    receber_callback = servico.receber
    if controlador is not None:
        from src.comum.falhas import envolver_recebimento

        receber_callback = envolver_recebimento(controlador, servico.receber)

    @app.post(CAMINHO_MENSAGEM)
    async def receber(corpo: dict = Body(...)) -> JSONResponse:  # pragma: no cover - via servidor
        from src.comum.falhas import NoEmCrashError

        mensagem = Mensagem.de_json(corpo)
        try:
            return JSONResponse(content=dict(receber_callback(mensagem)))
        except NoEmCrashError as erro:
            raise HTTPException(status_code=503, detail=str(erro)) from erro

    @app.get("/estado")
    async def estado() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content=servico.instantaneo())

    @app.get("/saude")
    async def saude() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content={"ok": True, "papel": "participante", "no": servico._eu.id})

    if controlador is not None:
        from src.comum.falhas import registrar_endpoints_falha

        registrar_endpoints_falha(app, controlador)

    return app


# Objeto de app para o uvicorn do contêiner: `uvicorn src.doispc.participante:app`.
# Criado preguiçosamente só quando o módulo roda como alvo do servidor, para que
# importar o módulo em teste não exija as variáveis de ambiente.
def _app_de_env():  # pragma: no cover - caminho de contêiner
    return criar_app()
