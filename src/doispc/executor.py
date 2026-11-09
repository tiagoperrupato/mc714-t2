"""Executor de `Acao` do 2PC: a ponte entre a lógica pura e os efeitos colaterais.

A Máquina_de_Estados do 2PC devolve uma lista ordenada de `Acao` (`GravarLog`,
`Aplicar`, `Enviar`). Este executor as cumpre **na ordem exata** em que vêm:
grava e aplica na hora, e põe cada `Enviar` na fila de `src.comum.despacho`, que
o envia depois, fora do handler. Como a Máquina_de_Estados sempre emite a
`GravarLog` antes do `Enviar`/`Aplicar` que dela dependem, a entrada já está no
Log_Duravel quando a Mensagem entra na fila.

O executor concentra os três tipos de efeito num só lugar, de modo que
`coordenador.py` e `participante.py` fiquem adaptadores finos, sem duplicar o
despacho de rede nem a gravação durável.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from src.comum.despacho import Despachante
from src.comum.log import LogEstruturado
from src.comum.rede import ClienteRede, EnvioOk
from src.doispc.log_duravel import LogDuravel
from src.doispc.maquina import Acao, Aplicar, Enviar, GravarLog


class ExecutorAcoes:
    """Cumpre as `Acao` da Máquina_de_Estados preservando a ordem write-ahead.

    `enderecos` resolve o id de um Nó destino para a URL base do seu servidor
    HTTP, usada pelo `ClienteRede`. `deadline` é o prazo obrigatório de cada
    envio. `ao_aplicar`, se fornecido, é chamado para cada
    `Aplicar` (efeito local do COMMIT/ABORT), além do registro no log.
    """

    def __init__(
        self,
        *,
        no_id: int,
        log_duravel: LogDuravel,
        cliente: ClienteRede,
        deadline: float,
        enderecos: Mapping[int, str],
        log: Optional[LogEstruturado] = None,
        ao_aplicar: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._no_id = no_id
        self._log_duravel = log_duravel
        self._cliente = cliente
        self._deadline = deadline
        self._enderecos = dict(enderecos)
        self._log = log
        self._ao_aplicar = ao_aplicar
        self._saida: Despachante[Enviar] = Despachante(self.enviar)

    def executar(self, acoes: Sequence[Acao]) -> None:
        """Grava e aplica na hora e enfileira os envios, na ordem das `Acao`.

        Uma falha ao gravar interrompe a sequência antes dos envios que dependem
        da entrada. O resultado de cada envio só aparece no evento `envia` do log
        estruturado.
        """
        for acao in acoes:
            if isinstance(acao, GravarLog):
                self._gravar(acao)
            elif isinstance(acao, Aplicar):
                self._aplicar(acao)
            elif isinstance(acao, Enviar):
                self._saida.enfileirar([acao])

    def _gravar(self, acao: GravarLog) -> None:
        """Grava a entrada no Log_Duravel; só retorna quando ela está durável."""
        self._log_duravel.registrar(acao.entrada)
        if self._log is not None:
            self._log.evento(
                "grava_log",
                transacao=acao.entrada.transacao,
                fase=acao.entrada.fase,
                valor=acao.entrada.valor,
            )

    def _aplicar(self, acao: Aplicar) -> None:
        """Aplica localmente o efeito da Decisao (COMMIT/ABORT)."""
        if self._ao_aplicar is not None:
            self._ao_aplicar(acao.transacao, acao.decisao)
        if self._log is not None:
            self._log.evento("aplica", transacao=acao.transacao, decisao=acao.decisao)

    def enviar(self, acao: Enviar) -> None:
        """Envia a Mensagem já, na thread de quem chama, com o Deadline obrigatório.

        A fila de saída chama este método, e a retransmissão do Coordenador também.
        """
        destino = acao.mensagem.destino
        url = self._enderecos.get(destino)
        if url is None:
            raise KeyError(
                f"sem endereço para o Nó {destino}; conhecidos: {sorted(self._enderecos)}"
            )
        resultado = self._cliente.enviar(url, acao.mensagem, self._deadline)
        if self._log is not None:
            self._log.evento(
                "envia",
                msg_tipo=acao.mensagem.tipo,
                destino=destino,
                transacao=acao.mensagem.corpo.get("transacao"),
                resultado=type(resultado).__name__,
                ok=isinstance(resultado, EnvioOk),
            )
