"""Ponto de entrada dos Nós do 2PC: escolhe o papel pelo ambiente e sobe o servidor.

O contêiner executa `python -m src.doispc`. A variável `DOISPC_PAPEL` seleciona
se este Nó é o Coordenador ou um Participante, de modo que a MESMA imagem sirva
os dois papéis — o algoritmo é escolhido pelo ambiente, nunca embutido no código.

Toda a configuração vem de variáveis de ambiente e é validada antes de abrir
socket ou tocar o disco (Requisito 1.2, 1.3): uma configuração inválida encerra
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

Participante:
- `PART_ID`, `PART_PORTA`    identidade e porta de escuta.
- `COORD_ID/HOST/PORTA`      identidade do Coordenador.
- `DOISPC_VOTO`              "SIM" ou "NAO" — a flag de voto (Requisito 7.4).
- `DOISPC_DEADLINE`          Deadline de cada envio de rede, em s (default 2.0).
- `DOISPC_LOG_DURAVEL`       caminho do Log_Duravel (default /dados/participante-{id}.log).
"""

from __future__ import annotations

import os
from pathlib import Path

from src.comum.config import ConfigInvalidaError, ConfigNo
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import abrir_log
from src.comum.rede import ClienteRede
from src.doispc.coordenador import (
    ServicoCoordenador,
    _participantes_de_env,
    criar_app as criar_app_coordenador,
)
from src.doispc.log_duravel import VOTO_NAO, VOTO_SIM, LogDuravelArquivo
from src.doispc.participante import (
    ServicoParticipante,
    criar_app as criar_app_participante,
)
from src.comum.modelos import Mensagem, No
from src.comum.rede import EnvioOk, ResultadoEnvio
from src.doispc.maquina import MSG_DECISAO


class _ClienteSuprimeDecisao:
    """Cliente do Coordenador que entrega tudo, menos a DECISAO (cenário de crash).

    Modela, de forma determinística e reproduzível, o Coordenador que morre
    depois de coletar os Votos e gravar a Decisao (write-ahead), mas antes de a
    DECISAO alcançar qualquer Participante (Requisito 8.5). Os PREPARE passam —
    os Votos voltam normalmente —, mas cada Mensagem de tipo `decisao` é
    descartada antes de tocar a rede, como se o processo tivesse morrido entre
    decidir e difundir. É a mesma técnica do teste
    `test_crash_do_coordenador_deixa_participantes_em_duvida`, trazida ao
    contêiner por uma flag de ambiente (`DOISPC_SUPRIMIR_DECISAO`), para que o
    cenário de bloqueio seja repetível sobre o compose sem corrida de timing.

    Os Participantes que votaram SIM ficam em Estado_Em_Duvida: votaram, mas não
    conhecem a Decisao. Nos logs isso aparece como Voto SIM durável sem Decisao
    durável, que o invariante 5 classifica como **aviso**, não reprovação
    (Requisito 12.4).
    """

    def __init__(self, cliente) -> None:
        self._cliente = cliente

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        if mensagem.tipo == MSG_DECISAO:
            return EnvioOk(status=200, corpo={"ack": True, "descartada": True})
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        fechar = getattr(self._cliente, "fechar", None)
        if callable(fechar):
            fechar()


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

    O `inicio_replica` passa a sair do próprio controlador, que carimba a Semente
    e o Plano_de_Falhas corrente (Requisito 8.4). Os streams `drop`/`delay` do
    controlador derivam da Semente, mantendo o Cenário de falha reproduzível
    (Requisito 8.2).
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

    # Flag de cenário: suprime o despacho da DECISAO (Requisito 8.5). Reproduz o
    # Coordenador que cai entre decidir e difundir, deixando os Participantes
    # que votaram SIM em Estado_Em_Duvida, de forma determinística sobre o
    # compose. Fora deste cenário a flag fica desligada e o Coordenador opera
    # normalmente.
    suprimir_decisao = os.environ.get("DOISPC_SUPRIMIR_DECISAO", "").strip().lower() in (
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
            "suprimir_decisao": suprimir_decisao,
        },
    )
    cliente_base = ClienteComFalhas(ClienteRede(), controlador)
    cliente = _ClienteSuprimeDecisao(cliente_base) if suprimir_decisao else cliente_base
    servico = ServicoCoordenador(
        eu,
        participantes,
        log_duravel=LogDuravelArquivo(caminho_log),
        cliente=cliente,
        deadline_voto=deadline_voto,
        deadline_decisao=deadline_decisao,
        log=log,
    )
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
    # Recupera eventual Decisao de uma transação anterior gravada no volume
    # (Requisito 6.6): relê o Log_Duravel de todas as transações conhecidas.
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
