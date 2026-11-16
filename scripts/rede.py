"""CLI de falhas de rede: cria os proxies do Toxiproxy, aplica latência e particiona.

Fala com o HTTP API do Toxiproxy (serviço `toxiproxy` do compose, publicado em
`127.0.0.1:8474`). Não sobe nem derruba contêiner: supõe o compose no ar com o
override `compose.toxiproxy.yaml`, que faz os Nós falarem pelas escutas do Toxiproxy.

Subcomandos:

    python -m scripts.rede anel semear
        Cria os 5 proxies do Anel (toxiproxy:16000+i -> no-anel-i:8000).

    python -m scripts.rede anel latencia --ms 300 [--jitter MS] [--no I]
        Atrasa em 300 ms as respostas que voltam por cada proxy do Anel, ou só pelo
        do Nó I. Acima do Deadline de repasse (1 s no compose), quem envia ao I
        estoura o prazo e o salta, mesmo com o I vivo.

    python -m scripts.rede anel particionar --no 5
        Desativa o proxy do Nó 5, e nenhuma Mensagem chega mais a ele. A próxima
        eleição o salta e elege o maior id entre os outros.

    python -m scripts.rede anel curar [--no I]
        Remove os toxics e reativa os proxies do Anel, ou só o do Nó I.

    python -m scripts.rede anel listar
        Lista os proxies registrados, ligados ou não, e os toxics de cada um.

    python -m scripts.rede doispc semear
        Cria os proxies do Coordenador e dos 3 Participantes.

    python -m scripts.rede doispc particionar --participante 1
        Desativa o proxy do Participante 1 (id 11), e as Mensagens do Coordenador
        não chegam a ele. Uma transação feita durante a partição termina em ABORT
        no prazo de coleta de Votos.

    python -m scripts.rede doispc latencia --ms 300 [--jitter MS] [--participante K]
    python -m scripts.rede doispc curar [--participante K]

Sem `--no` ou `--participante`, latencia, particionar e curar valem para todos os proxies do
algoritmo, e no 2PC isso inclui o do Coordenador. Desativar o proxy de um Nó corta só o que
chega a ele. Isso isola um Nó do Anel ou um Participante, porque eles só enviam em resposta
ao que recebem. Sem `--jitter`, a latência é fixa e se repete sem Semente. O jitter é sorteado
pelo próprio Toxiproxy, fora da Semente do projeto.
"""

from __future__ import annotations

import argparse
import sys

from src.comum.toxiproxy import (
    API_PADRAO,
    ClienteToxiproxy,
    Proxy,
    ToxiproxyError,
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
    try:
        return args.func(args)
    except ToxiproxyError as erro:
        print(
            f"[rede] {erro}. Suba a pilha com o Toxiproxy: "
            "docker compose -f compose.yaml -f compose.toxiproxy.yaml up -d --wait",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
