"""Cliente do HTTP API do Toxiproxy, sem subir contêiner.

Estes testes exercitam `ClienteToxiproxy` contra um `httpx.MockTransport` que
imita o HTTP API: cada requisição é casada por método+caminho e devolve uma
resposta gravada, registrando o corpo enviado para que as asserções verifiquem
o JSON exato que o cliente manda ao Toxiproxy. Assim validamos o contrato do
cliente (criar/remover proxies, aplicar toxics, partição via enabled=false,
idempotência do `semear`) sem depender de Docker.

A topologia de proxies dos dois algoritmos (`proxies_anel`, `proxies_2pc`) é
testada à parte: os nomes e as portas de escuta têm de casar exatamente com o
`compose.toxiproxy.yaml` e com `scripts/rede.py`, então um teste os fixa como
contrato.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.comum.toxiproxy import (
    PORTA_2PC_BASE,
    PROXY_2PC_COORDENADOR,
    ClienteToxiproxy,
    Proxy,
    ToxiproxyError,
    nome_proxy_anel,
    nome_proxy_participante,
    proxies_2pc,
    proxies_anel,
)


class _ApiFake:
    """Mini-servidor do HTTP API do Toxiproxy sobre httpx.MockTransport.

    Mantém um registro em memória de proxies (nome -> descrição) e responde às
    rotas que o cliente usa: /version, GET/POST/DELETE /proxies, POST
    /proxies/{n} (toggle), /reset e os toxics. Registra cada requisição em
    `chamadas` para inspeção nos testes.
    """

    def __init__(self) -> None:
        self.proxies: dict[str, dict] = {}
        self.chamadas: list[tuple[str, str, dict | None]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def _handler(self, req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else None
        self.chamadas.append((req.method, req.url.path, corpo))
        path = req.url.path

        if path == "/version":
            return httpx.Response(200, text="2.9.0")
        if path == "/reset" and req.method == "POST":
            for p in self.proxies.values():
                p["toxics"] = []
                p["enabled"] = True
            return httpx.Response(204)
        if path == "/proxies" and req.method == "GET":
            return httpx.Response(200, json=self.proxies)
        if path == "/proxies" and req.method == "POST":
            self.proxies[corpo["name"]] = {
                "name": corpo["name"],
                "listen": corpo["listen"],
                "upstream": corpo["upstream"],
                "enabled": corpo.get("enabled", True),
                "toxics": [],
            }
            return httpx.Response(201, json=self.proxies[corpo["name"]])

        # /proxies/{nome}  e  /proxies/{nome}/toxics[/{toxic}]
        partes = path.strip("/").split("/")
        if partes[0] == "proxies" and len(partes) == 2:
            nome = partes[1]
            if req.method == "DELETE":
                if nome not in self.proxies:
                    return httpx.Response(404, text="not found")
                del self.proxies[nome]
                return httpx.Response(204)
            if req.method == "GET":
                if nome not in self.proxies:
                    return httpx.Response(404)
                return httpx.Response(200, json=self.proxies[nome])
            if req.method == "POST":  # toggle enabled
                self.proxies[nome]["enabled"] = corpo.get("enabled", True)
                return httpx.Response(200, json=self.proxies[nome])
        if partes[0] == "proxies" and len(partes) >= 3 and partes[2] == "toxics":
            nome = partes[1]
            if req.method == "POST":
                toxic = {"name": corpo.get("name", corpo["type"]), **corpo}
                self.proxies[nome]["toxics"].append(toxic)
                return httpx.Response(200, json=toxic)
            if req.method == "DELETE":
                alvo = partes[3]
                self.proxies[nome]["toxics"] = [
                    t for t in self.proxies[nome]["toxics"] if t["name"] != alvo
                ]
                return httpx.Response(204)
        return httpx.Response(404, text=f"rota não mapeada: {req.method} {path}")


def _cliente(api: _ApiFake) -> ClienteToxiproxy:
    return ClienteToxiproxy(transporte=httpx.Client(transport=api.transport()))


# ---------------------------------------------------------------------------
# Proxies: criar, remover, idempotência
# ---------------------------------------------------------------------------


def test_criar_proxy_envia_payload_esperado() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("anel-1", "0.0.0.0:16001", "no-anel-1:8000"))
    assert "anel-1" in api.proxies
    assert api.proxies["anel-1"]["listen"] == "0.0.0.0:16001"
    assert api.proxies["anel-1"]["upstream"] == "no-anel-1:8000"


def test_criar_proxy_e_idempotente_removendo_homonimo() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("anel-1", "0.0.0.0:16001", "no-anel-1:8000"))
        # Recriar com upstream diferente substitui, não duplica.
        cli.criar_proxy(Proxy("anel-1", "0.0.0.0:16001", "outro:8000"))
    assert list(api.proxies.keys()) == ["anel-1"]
    assert api.proxies["anel-1"]["upstream"] == "outro:8000"


def test_semear_reseta_antes_de_recriar() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("resto", "0.0.0.0:9999", "velho:1"))
        cli.semear(proxies_anel((1, 2, 3, 4, 5)))
    # O proxy antigo some; ficam só os 5 do Anel.
    assert set(api.proxies) == {f"anel-{i}" for i in range(1, 6)}


# ---------------------------------------------------------------------------
# Toxics e partição
# ---------------------------------------------------------------------------


def test_latencia_aplica_toxic_com_atributos() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("anel-3", "0.0.0.0:16003", "no-anel-3:8000"))
        cli.latencia("anel-3", 300, 20)
    toxics = api.proxies["anel-3"]["toxics"]
    assert len(toxics) == 1
    tox = toxics[0]
    assert tox["type"] == "latency"
    assert tox["attributes"] == {"latency": 300, "jitter": 20}


def test_particionar_desabilita_proxy() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("anel-5", "0.0.0.0:16005", "no-anel-5:8000"))
        cli.definir_habilitado("anel-5", False)
    assert api.proxies["anel-5"]["enabled"] is False


def test_limpar_toxics_remove_todos() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        cli.criar_proxy(Proxy("anel-3", "0.0.0.0:16003", "no-anel-3:8000"))
        cli.latencia("anel-3", 300)
        cli.latencia("anel-3", 300, direcao="upstream")
        assert len(api.proxies["anel-3"]["toxics"]) == 2
        cli.limpar_toxics("anel-3")
    assert api.proxies["anel-3"]["toxics"] == []


def test_erro_de_api_vira_toxiproxyerror() -> None:
    api = _ApiFake()
    with _cliente(api) as cli:
        # O GET de um proxy inexistente devolve 404, que vira ToxiproxyError.
        with pytest.raises(ToxiproxyError):
            cli.limpar_toxics("nao-existe")


# ---------------------------------------------------------------------------
# Topologia: contrato de nomes e portas (tem de casar com o compose/override)
# ---------------------------------------------------------------------------


def test_topologia_anel_mapeia_ids_para_portas() -> None:
    proxies = proxies_anel((1, 2, 3, 4, 5))
    assert [p.nome for p in proxies] == [f"anel-{i}" for i in range(1, 6)]
    assert [p.nome for p in proxies] == [nome_proxy_anel(i) for i in range(1, 6)]
    assert {p.listen for p in proxies} == {f"0.0.0.0:{16000 + i}" for i in range(1, 6)}
    # O upstream de cada proxy aponta ao serviço do compose correspondente.
    assert proxies[0].upstream == "no-anel-1:8000"
    assert proxies[4].upstream == "no-anel-5:8000"


def test_topologia_2pc_coordenador_e_participantes() -> None:
    proxies = proxies_2pc((11, 12, 13))
    nomes = [p.nome for p in proxies]
    assert nomes == ["2pc-coordenador", "2pc-participante-1", "2pc-participante-2", "2pc-participante-3"]
    assert nomes == [PROXY_2PC_COORDENADOR, *(nome_proxy_participante(k) for k in (1, 2, 3))]
    coord = proxies[0]
    assert coord.listen == f"0.0.0.0:{PORTA_2PC_BASE}"
    assert coord.upstream == "coordenador:8000"
    # Participante id 11 -> escuta 17011, upstream participante-1:8000.
    assert proxies[1].listen == "0.0.0.0:17011"
    assert proxies[1].upstream == "participante-1:8000"
    assert proxies[3].listen == "0.0.0.0:17013"
