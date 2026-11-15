"""Cliente do HTTP API do Toxiproxy: latência e partição scriptadas e repetíveis.

O Toxiproxy é um proxy TCP entre os Nós. Com o override `compose.toxiproxy.yaml`, cada Nó fala
com a escuta (`listen`) do Toxiproxy que corresponde ao destino, e ela repassa ao Nó real
(`upstream`). O projeto aplica latência como toxic, e desativar o proxy de um Nó corta as
Mensagens que chegam a ele, a partição. Tudo passa pelo HTTP API (porta 8474), sem reiniciar
contêiner nem tocar o código dos algoritmos.

O Toxiproxy degrada o canal e complementa o `ControladorFalhas` de `src.comum.falhas`, que injeta
crash, pause, delay e drop na aplicação. O crash fica lá porque o Toxiproxy não representa a morte
do processo. As Máquinas_de_Estados não importam este módulo. Quem o usa são `scripts/rede.py`,
a demo de rede (`scripts/demo_rede_toxiproxy.py`), o cenário `2pc particao` de
`scripts/cenario.py`, a bateria do 2PC (`src/experimentos/condutor_doispc.py`) e os testes de
integração.

Latência acima do Deadline faz o `ClienteRede` devolver `EnvioTimeout`, e o proxy desativado
devolve `EnvioConexaoRecusada` ou `EnvioIndisponivel`. O Anel lê os dois como Sucessor falho. O
2PC só os registra no evento `envia`, e o ABORT vem do prazo de coleta de Votos.

Referência do API: https://github.com/Shopify/toxiproxy#http-api
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

API_PADRAO = "http://127.0.0.1:8474"
TIMEOUT_API_S = 5.0


class ToxiproxyError(RuntimeError):
    """Falha ao falar com o HTTP API do Toxiproxy.

    Agrupa erros de transporte (serviço fora do ar) e respostas de erro do API
    (4xx/5xx), sempre com o contexto suficiente para o chamador diagnosticar.
    Este módulo só a captura para tentar de novo em `esperar_pronto` e para
    ignorar a remoção de um proxy ou toxic já ausente. Fora dele ninguém a
    trata, então ela interrompe o script, a bateria ou o teste que usa o
    cliente, e uma falha de setup não se confunde com o comportamento do
    algoritmo sob falha.
    """


@dataclass(frozen=True)
class Proxy:
    """Um proxy nomeado: a porta de escuta e o destino real para o qual repassa.

    `nome` identifica o proxy no API. `listen` é o endereço host:porta onde o
    Toxiproxy aceita conexões (os Nós apontam para cá), e `upstream` é o endereço
    host:porta do Nó real para onde o tráfego é repassado. O proxy nasce
    habilitado. Desativado por `definir_habilitado`, nada mais chega ao
    `upstream`: é a partição de entrada do Nó.
    """

    nome: str
    listen: str
    upstream: str

    def para_json(self) -> dict[str, Any]:
        return {
            "name": self.nome,
            "listen": self.listen,
            "upstream": self.upstream,
            "enabled": True,
        }


class ClienteToxiproxy:
    """Cliente mínimo do HTTP API do Toxiproxy (criar proxies e aplicar toxics).

    `base_url` aponta para o API (por padrão a porta 8474 do serviço no
    compose). O cliente é reutilizável, e `fechar` libera a conexão httpx. Todos
    os métodos levantam `ToxiproxyError` em falha de transporte ou resposta de
    erro, para que o setup de um Cenário de rede falhe alto e cedo, distinto do
    comportamento do algoritmo que o Cenário pretende observar.

    As operações são **idempotentes onde importa**: `criar_proxy` substitui um
    proxy homônimo preexistente e `semear` limpa o estado antes de recriar, de
    modo que rodar o mesmo Cenário duas vezes produza a mesma topologia de
    proxies, que é o que torna o Cenário de rede repetível.
    """

    def __init__(
        self,
        base_url: str = API_PADRAO,
        *,
        transporte: Optional[httpx.Client] = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._proprio = transporte is None
        self._http = transporte or httpx.Client()

    # -- infraestrutura -----------------------------------------------------

    def _req(self, metodo: str, caminho: str, *, json: Any = None) -> httpx.Response:
        url = self._base + caminho
        try:
            resp = self._http.request(metodo, url, json=json, timeout=TIMEOUT_API_S)
        except httpx.HTTPError as erro:  # transporte fora do ar
            raise ToxiproxyError(f"{metodo} {url} falhou no transporte: {erro}") from erro
        if resp.status_code >= 400:
            raise ToxiproxyError(
                f"{metodo} {url} retornou HTTP {resp.status_code}: {resp.text}"
            )
        return resp

    def esperar_pronto(self, deadline: float = 30.0) -> None:
        """Bloqueia até o API responder, ou levanta `ToxiproxyError` no Deadline.

        Útil logo após `docker compose up`: o contêiner do Toxiproxy pode levar
        um instante para abrir o API. Respeita um Deadline total, como o
        Transporte do projeto, e nunca espera para sempre.
        """
        limite = time.monotonic() + deadline
        ultimo: Optional[Exception] = None
        while time.monotonic() < limite:
            try:
                self._req("GET", "/version")
                return
            except ToxiproxyError as erro:
                ultimo = erro
                time.sleep(0.25)
        raise ToxiproxyError(f"Toxiproxy não respondeu em {deadline}s: {ultimo}")

    # -- proxies ------------------------------------------------------------

    def listar_proxies(self) -> dict[str, Any]:
        """Devolve o mapa nome -> descrição de todos os proxies registrados."""
        return self._req("GET", "/proxies").json()

    def criar_proxy(self, proxy: Proxy) -> None:
        """Cria (ou recria) `proxy`. Remove um homônimo antes, p/ idempotência."""
        self._remover_proxy(proxy.nome)
        self._req("POST", "/proxies", json=proxy.para_json())

    def _remover_proxy(self, nome: str) -> None:
        """Remove o proxy `nome`, ignorando o 404 de um proxy já ausente."""
        try:
            self._req("DELETE", f"/proxies/{nome}")
        except ToxiproxyError:
            pass

    def definir_habilitado(self, nome: str, habilitado: bool) -> None:
        """Liga ou desliga o proxy. Desligado, ele fecha a escuta e nada chega ao Nó por ela.

        O `ClienteRede` de quem envia devolve `EnvioConexaoRecusada` ou
        `EnvioIndisponivel`, que o Anel lê como Sucessor falho. No 2PC o resultado
        só vai ao evento `envia`, e o ABORT vem do prazo de coleta de Votos.
        """
        self._req("POST", f"/proxies/{nome}", json={"enabled": habilitado})

    def resetar(self) -> None:
        """Zera o estado do Toxiproxy: remove todos os proxies e toxics.

        Chamado no início de um `semear` para garantir que a topologia de uma
        execução não vaze para a próxima (repetibilidade).
        """
        self._req("POST", "/reset")
        for nome in list(self.listar_proxies().keys()):
            self._remover_proxy(nome)

    # -- toxics -------------------------------------------------------------

    def latencia(
        self, proxy: str, ms: int, jitter_ms: int = 0, *, direcao: str = "downstream"
    ) -> None:
        """Injeta `ms` de latência (com `jitter_ms` opcional) na rota do proxy.

        `direcao` é `downstream` (upstream->cliente) ou `upstream` (cliente->upstream).
        Latência acima do Deadline do `ClienteRede` faz a chamada estourar e
        devolver `EnvioTimeout`. O nome do toxic leva a direção,
        para que os dois sentidos de um proxy tenham latência ao mesmo tempo.
        """
        corpo = {
            "name": f"latency_{direcao}",
            "type": "latency",
            "stream": direcao,
            "toxicity": 1.0,
            "attributes": {"latency": int(ms), "jitter": int(jitter_ms)},
        }
        self._req("POST", f"/proxies/{proxy}/toxics", json=corpo)

    def limpar_toxics(self, proxy: str) -> None:
        """Remove todos os toxics de `proxy`, deixando a rota limpa."""
        descricao = self._req("GET", f"/proxies/{proxy}").json()
        for toxic in descricao.get("toxics", []):
            try:
                self._req("DELETE", f"/proxies/{proxy}/toxics/{toxic['name']}")
            except ToxiproxyError:
                pass

    # -- alto nível ---------------------------------------------------------

    def semear(self, proxies: list[Proxy]) -> None:
        """Reseta e recria o conjunto `proxies` do zero, de forma repetível.

        Dada a lista de proxies de um algoritmo, zera o estado e os cria, e duas
        execuções do mesmo Cenário partem da mesma topologia.
        """
        self.resetar()
        for proxy in proxies:
            self.criar_proxy(proxy)

    def fechar(self) -> None:
        if self._proprio:
            self._http.close()

    def __enter__(self) -> "ClienteToxiproxy":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.fechar()


# ---------------------------------------------------------------------------
# Topologia de proxies dos dois algoritmos
# ---------------------------------------------------------------------------
#
# As funções abaixo descrevem, em um só lugar, QUAIS proxies o compose expõe e
# como os ids dos Nós mapeiam para as portas de escuta do Toxiproxy. Os valores
# têm de casar exatamente com o `compose.toxiproxy.yaml` (as portas em `URLS`,
# `DOISPC_PARTICIPANTES`, `COORD_HOST/PORTA` apontadas para o Toxiproxy).
# Mantê-los aqui evita números mágicos espalhados.

# Porta base das escutas do Anel: o Nó de id `i` é alcançado via `PORTA_ANEL_BASE + i`.
PORTA_ANEL_BASE = 16000
# Porta base das escutas do 2PC: o Coordenador escuta na base e o Participante de id `p` em
# base + p (11 -> 17011, ...).
PORTA_2PC_BASE = 17000
PROXY_2PC_COORDENADOR = "2pc-coordenador"


def nome_proxy_anel(no_id: int) -> str:
    """Nome do proxy que leva ao Nó `no_id` do Anel."""
    return f"anel-{no_id}"


def nome_proxy_participante(ordem: int) -> str:
    """Nome do proxy que leva ao Participante de posição `ordem` (1, 2, 3)."""
    return f"2pc-participante-{ordem}"


def proxies_anel(ids: tuple[int, ...]) -> list[Proxy]:
    """Proxies do Anel, um por Nó: `toxiproxy:(16000 + i) -> no-anel-i:8000`.

    Quem repassa ao Sucessor `i` usa essa escuta, então todo o tráfego do Anel
    atravessa o Toxiproxy. Desativar o proxy do Líder corta as Mensagens para ele.
    """
    return [
        Proxy(
            nome=nome_proxy_anel(i),
            listen=f"0.0.0.0:{PORTA_ANEL_BASE + i}",
            upstream=f"no-anel-{i}:8000",
        )
        for i in ids
    ]


def proxies_2pc(participantes: tuple[int, ...]) -> list[Proxy]:
    """Proxies do 2PC: um para o Coordenador e um por Participante.

    O do Coordenador é `toxiproxy:17000 -> coordenador:8000`, e o do Participante
    de id `p` é `toxiproxy:(17000 + p) -> participante-k:8000`, com `k` a posição
    de `p` em `participantes`. Desativar o proxy de um Participante corta as
    Mensagens do Coordenador a ele. As que ele envia passam pelo proxy do
    Coordenador.
    """
    proxies = [
        Proxy(
            nome=PROXY_2PC_COORDENADOR,
            listen=f"0.0.0.0:{PORTA_2PC_BASE}",
            upstream="coordenador:8000",
        )
    ]
    for ordem, pid in enumerate(participantes, start=1):
        proxies.append(
            Proxy(
                nome=nome_proxy_participante(ordem),
                listen=f"0.0.0.0:{PORTA_2PC_BASE + pid}",
                upstream=f"participante-{ordem}:8000",
            )
        )
    return proxies
