"""CLI de falhas de rede: cria proxies do Toxiproxy e aplica toxics.

Este script é o orquestrador scriptado e repetível das falhas de rede: fala
com o HTTP API do Toxiproxy (serviço `toxiproxy` do compose, publicado em
`127.0.0.1:8474`) para semear os proxies entre os Nós e, sobre eles, aplicar
latência e partição.

Não sobe nem derruba contêiner: assume que o compose já está no ar com o
override de roteamento (`compose.toxiproxy.yaml`), de modo que os Nós já apontam
para as portas de escuta do Toxiproxy. A fronteira de projeto é respeitada: este
é um script de infra/experimento, não parte das Máquinas_de_Estados.

Subcomandos:

    python -m scripts.rede anel semear
        Cria os 5 proxies do Anel (toxiproxy:1600i -> no-anel-i:8000).

    python -m scripts.rede anel latencia --ms 300 [--no I]
        Injeta 300 ms de latência em todas as rotas do Anel (ou só no Nó I).
        Com ms > Deadline, o cliente estoura o Deadline.

    python -m scripts.rede anel particionar --no 5
        Desabilita o proxy do Nó 5 (partição do Líder do resto); os Nós vivos
        disparam e concluem uma nova eleição.

    python -m scripts.rede anel curar [--no I]
        Remove toxics e reabilita o(s) proxy(s) do Anel.

    python -m scripts.rede doispc semear
        Cria os proxies do Coordenador e dos 3 Participantes.

    python -m scripts.rede doispc particionar --participante 1
        Desabilita o proxy do Participante 1 (partição Coordenador<->Part. 1);
        o comportamento resultante fica registrado no log.

    python -m scripts.rede doispc latencia --ms 300 [--participante K]
    python -m scripts.rede doispc curar [--participante K]
"""

from __future__ import annotations

import argparse
import sys

from src.comum.toxiproxy import (
    API_PADRAO,
    ClienteToxiproxy,
    Proxy,
    nome_proxy_anel,
    nome_proxy_participante,
    proxies_2pc,
    proxies_anel,
)
from src.experimentos.conteineres import PORTAS_ANEL, PORTAS_PART

ANEL_IDS = tuple(PORTAS_ANEL)
PART_IDS = tuple(PORTAS_PART)


def _cliente(args: argparse.Namespace) -> ClienteToxiproxy:
    cli = ClienteToxiproxy(args.api)
    cli.esperar_pronto(deadline=args.espera)
    return cli


def _proxies_do_algoritmo(algoritmo: str) -> list[Proxy]:
    if algoritmo == "anel":
        return proxies_anel(ANEL_IDS)
    return proxies_2pc(PART_IDS)


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------


def _semear(args: argparse.Namespace) -> int:
    with _cliente(args) as cli:
        proxies = _proxies_do_algoritmo(args.algoritmo)
        cli.semear(proxies)
        nomes = ", ".join(p.nome for p in proxies)
        print(f"[rede] semeados {len(proxies)} proxies do {args.algoritmo}: {nomes}")
    return 0


def _alvos(args: argparse.Namespace) -> list[str]:
    """O proxy do Nó ou do Participante pedido, ou todos os do algoritmo, Coordenador incluído."""
    if args.algoritmo == "anel" and args.no is not None:
        return [nome_proxy_anel(args.no)]
    if args.algoritmo == "doispc" and args.participante is not None:
        return [nome_proxy_participante(args.participante)]
    return [p.nome for p in _proxies_do_algoritmo(args.algoritmo)]


def _latencia(args: argparse.Namespace) -> int:
    with _cliente(args) as cli:
        for nome in _alvos(args):
            cli.limpar_toxics(nome)
            cli.latencia(nome, args.ms, args.jitter)
            print(f"[rede] {nome}: latência {args.ms} ms (jitter {args.jitter} ms)")
    return 0


def _particionar(args: argparse.Namespace) -> int:
    with _cliente(args) as cli:
        for nome in _alvos(args):
            cli.definir_habilitado(nome, False)
            print(f"[rede] {nome}: proxy DESABILITADO (partição de rede)")
    return 0


def _curar(args: argparse.Namespace) -> int:
    with _cliente(args) as cli:
        for nome in _alvos(args):
            cli.limpar_toxics(nome)
            cli.definir_habilitado(nome, True)
            print(f"[rede] {nome}: toxics removidos e proxy reabilitado")
    return 0


def _listar(args: argparse.Namespace) -> int:
    with _cliente(args) as cli:
        proxies = cli.listar_proxies()
        if not proxies:
            print("[rede] nenhum proxy registrado")
            return 0
        for nome, desc in sorted(proxies.items()):
            toxics = ", ".join(t["name"] for t in desc.get("toxics", [])) or "—"
            estado = "on" if desc.get("enabled", True) else "OFF"
            print(f"[rede] {nome}: {desc['listen']} -> {desc['upstream']} [{estado}] toxics={toxics}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("algoritmo", choices=["anel", "doispc"], help="algoritmo alvo")
    parser.add_argument("--api", default=API_PADRAO, help="URL do HTTP API do Toxiproxy")
    parser.add_argument("--espera", type=float, default=30.0, help="Deadline de espera pelo API, em s")

    sub = parser.add_subparsers(dest="acao", required=True)

    sub.add_parser("semear", help="cria os proxies do algoritmo").set_defaults(func=_semear)
    sub.add_parser("listar", help="lista proxies e toxics correntes").set_defaults(func=_listar)

    p_lat = sub.add_parser("latencia", help="injeta latência nas rotas")
    p_lat.add_argument("--ms", type=int, required=True, help="latência em milissegundos")
    p_lat.add_argument("--jitter", type=int, default=0, help="jitter em milissegundos")
    p_lat.add_argument("--no", type=int, choices=ANEL_IDS, help="Anel: só o Nó I (default: todos)")
    p_lat.add_argument("--participante", type=int, choices=(1, 2, 3), help="2PC: só o Participante K=1..3")
    p_lat.set_defaults(func=_latencia)

    p_par = sub.add_parser("particionar", help="desabilita proxy(s) = partição")
    p_par.add_argument("--no", type=int, choices=ANEL_IDS, help="Anel: só o Nó I (default: todos)")
    p_par.add_argument("--participante", type=int, choices=(1, 2, 3), help="2PC: só o Participante K=1..3")
    p_par.set_defaults(func=_particionar)

    p_cur = sub.add_parser("curar", help="remove toxics e reabilita proxy(s)")
    p_cur.add_argument("--no", type=int, choices=ANEL_IDS, help="Anel: só o Nó I (default: todos)")
    p_cur.add_argument("--participante", type=int, choices=(1, 2, 3), help="2PC: só o Participante K=1..3")
    p_cur.set_defaults(func=_curar)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
