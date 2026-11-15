"""Cliente do HTTP API do Toxiproxy — falhas de rede scriptadas e repetíveis.

O Toxiproxy é um proxy TCP que fica **entre** os Nós: cada Nó, em vez de falar
direto com o peer, fala com uma porta de escuta (`listen`) do Toxiproxy, que
repassa ao destino real (`upstream`). O projeto aplica sobre esse proxy o
toxic de latência e, desabilitando o proxy inteiro, a partição de rede. Tudo
isso é controlado pelo HTTP API do serviço (porta 8474), sem reiniciar
contêiner e sem tocar o código dos algoritmos.

Por que este cliente vive em `src/comum/` e não nos algoritmos: é parte do
Transporte/infra, complementar ao `ControladorFalhas` de aplicação
(`src.comum.falhas`). Os dois são **distintos e componíveis**: o controlador
modela a morte do processo (crash/pause) e o descarte/atraso na borda da
aplicação; o Toxiproxy modela a degradação do **canal** (latência, perda,
partição) que o processo não controla. O design (seção "Toxiproxy entre os
Nós") mantém crash/pause na aplicação justamente porque o Toxiproxy não
representa a morte do processo.

As Máquinas_de_Estados permanecem puras: elas não importam este módulo. Quem o
usa é o orquestrador de rede (`scripts/rede.py`) e os testes de integração. A
latência acima do Deadline faz o `ClienteRede` (`src.comum.rede`) devolver
`EnvioTimeout`; o corte de conexão (partição) devolve `EnvioConexaoRecusada` ou
`EnvioIndisponivel` — é esse mapeamento de borda que deixa a falha de rede
dirigir o salto do Anel e o ABORT por timeout do 2PC.

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
    Os scripts de cenário tratam esta exceção como condição de setup, nunca a
    confundindo com o comportamento do algoritmo sob falha.
    """


@dataclass(frozen=True)
class Proxy:
    """Um proxy nomeado: a porta de escuta e o destino real para o qual repassa.

    `nome` identifica o proxy no API. `listen` é o endereço host:porta onde o
    Toxiproxy aceita conexões (os Nós apontam para cá), e `upstream` é o endereço
    host:porta do Nó real para onde o tráfego é repassado. O proxy nasce
    habilitado, e a partição o desliga com `definir_habilitado`.
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
    compose). O cliente é reutilizável; `fechar` libera a conexão httpx. Todos
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
        um instante para abrir o API. Respeita um Deadline total, no espírito do
        Transporte do projeto — nada de espera infinita.
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
        """Liga/desliga o proxy. Desligar (False) corta a rota = partição.

        Modela a partição de rede: com o proxy desabilitado
        o Toxiproxy fecha as conexões, e o `ClienteRede` traduz isso em
        `EnvioConexaoRecusada`/`EnvioIndisponivel`, que o Anel lê como Sucessor
        falho (salto) e o 2PC lê para decidir/registrar o bloqueio.
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

        É o ponto de entrada dos scripts de cenário: dada a lista de proxies de
        um algoritmo, zera o estado e os cria, garantindo que duas execuções do
        mesmo Cenário partam da mesma topologia.
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
# têm de casar exatamente com o `compose.yaml` (as portas em `URLS`,
# `DOISPC_PARTICIPANTES`, `COORD_HOST/PORTA` apontadas para o Toxiproxy) e com
# `scripts/rede.py`. Mantê-los aqui evita números mágicos espalhados.

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
    """Proxies do Anel: um por Nó, repassando `toxiproxy:1600i -> no-anel-i:8000`.

    Cada Nó `j`, ao repassar ao Sucessor `i`, usa `http://toxiproxy:1600i`, de
    modo que todo o tráfego entre Nós do Anel atravessa o Toxiproxy e pode ser
    degradado/particionado por proxy. Partição do Líder é
    desabilitar o proxy do Líder.
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

    O Coordenador é alcançado via `toxiproxy:17000 -> coordenador:8000`; cada
    Participante `p` via `toxiproxy:1700?` -> `participante-?:8000`. Assim tanto
    Coordenador->Participante quanto Participante->Coordenador passam pelo
    Toxiproxy, e particionar o Coordenador de um Participante é desabilitar o
    proxy daquele Participante.
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
