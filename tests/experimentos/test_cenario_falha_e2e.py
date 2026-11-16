"""Cenários de falha de ponta a ponta e sua verificação.

Dois níveis, como no resto do projeto:

- **Sem Docker**: valida a lógica do `scripts/cenario.py` e do `scripts/demo.py`
  que NÃO precisa de contêiner: a validação de modos, a montagem
  dos overrides de cenário com a Semente, o registro da Semente, a coleta de
  cada log uma vez, o código 1 quando o desfecho difere do esperado, e a prova
  de que um log com Participantes em Estado_Em_Duvida, como o que fica enquanto
  o Coordenador está fora, produz AVISO e não REPROVA através de
  `src.analise.invariantes`. Também cobre o caminho de
  degradação sem Docker (código 2).

- **Com Docker** (marcados `slow`): sobem cada cenário de falha de ponta a ponta
  sobre o compose, coletam os logs reais e conferem que `verificar.py` passa.
  No `anel reinicio`, o contêiner do Líder cai e volta sem memória, e a terceira
  eleição o reintegra. No `crash-coord`, o processo do Coordenador morre depois
  de decidir, os Participantes ficam em dúvida, a verificação feita com o
  Coordenador fora dá o aviso dos três, e o contêiner reiniciado os tira dela.
  O `crash-coord-part` reinicia também o Participante 11, em dúvida e depois de
  decidir, e o 05 confere a recuperação do Coordenador e a segunda do 11.
  No `2pc particao`, o Participante 11 fica sem receber Mensagens, o Coordenador
  aborta no prazo de Votos, e a retransmissão leva o ABORT ao 11 depois da cura.
  Eles PULAM de forma limpa sem Docker. Como subir vários stacks em paralelo pode
  estourar o `--wait`, estes testes devem rodar um de cada vez.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from scripts import cenario, demo, verificar
from src.analise import invariantes as inv
from src.experimentos import conteineres
from tests.apoio import escrever_jsonl


# --------------------------------------------------------------------------- #
# Sem Docker: validação de modos.
# --------------------------------------------------------------------------- #


def test_modos_de_modos_sao_aceitos() -> None:
    for algoritmo, modos in cenario.MODOS.items():
        for modo in modos:
            cenario._validar_modo(algoritmo, modo)


@pytest.mark.parametrize(("algoritmo", "modo"), [("2pc", "normal"), ("anel", "crash-coord")])
def test_modo_fora_de_modos_e_recusado(algoritmo: str, modo: str) -> None:
    with pytest.raises(ValueError):
        cenario._validar_modo(algoritmo, modo)


def test_modo_2pc_invalido_retorna_codigo_1(tmp_path: Path, monkeypatch) -> None:
    # Validação antes de qualquer efeito: nem chega a checar Docker.
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "2pc", "inexistente", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


def test_semente_negativa_retorna_codigo_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    codigo = cenario.executar_cenario(
        "anel", "normal", tmp_path, semente=-1, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 1


# --------------------------------------------------------------------------- #
# Sem Docker: overrides de cenário carregam a Semente e o modo certos.
# --------------------------------------------------------------------------- #


def test_override_2pc_commit_vota_sim() -> None:
    txt = cenario.override_2pc("commit", 7)
    assert 'DOISPC_VOTO: "SIM"' in txt
    assert 'DOISPC_CAIR_APOS_DECIDIR: "false"' in txt
    assert txt.count('SEMENTE: "7"') == 4  # coord + 3 participantes


def test_override_2pc_abort_participante3_vota_nao() -> None:
    txt = cenario.override_2pc("abort", 0)
    # Apenas o Participante 3 vota NAO.
    assert 'DOISPC_VOTO: "NAO"' in txt
    assert txt.count('DOISPC_VOTO: "SIM"') == 2


def test_override_2pc_crash_coord_derruba_o_coordenador() -> None:
    txt = cenario.override_2pc("crash-coord", 3)
    assert 'DOISPC_CAIR_APOS_DECIDIR: "true"' in txt
    # Todos votam SIM no cenário de crash (para ficarem em dúvida).
    assert txt.count('DOISPC_VOTO: "SIM"') == 3


def test_override_2pc_crash_coord_part_e_o_do_crash_coord() -> None:
    assert cenario.override_2pc("crash-coord-part", 3) == cenario.override_2pc("crash-coord", 3)


def test_override_2pc_particao_todos_votam_sim() -> None:
    # O ABORT da partição só pode vir do prazo de coleta de Votos, e não de um NAO.
    txt = cenario.override_2pc("particao", 0)
    assert txt.count('DOISPC_VOTO: "SIM"') == 3
    assert 'DOISPC_CAIR_APOS_DECIDIR: "false"' in txt


@pytest.mark.parametrize("servicos", [conteineres.SERVICOS_ANEL, conteineres.SERVICOS_BULLY])
def test_override_fixa_a_semente_nos_cinco_nos(servicos: list[str]) -> None:
    txt = cenario._override_semente(servicos, 42)
    assert txt.count('SEMENTE: "42"') == 5
    for servico in servicos:
        assert f"  {servico}:\n" in txt


# --------------------------------------------------------------------------- #
# Sem Docker: Participante em dúvida vira AVISO, não REPROVA.
# --------------------------------------------------------------------------- #


def _registro(base: Path, algoritmo: str, rotulo: str) -> dict:
    return json.loads(
        (base / f"cenario_{algoritmo}_{rotulo}.json").read_text(encoding="utf-8")
    )


def test_participante_em_duvida_vira_aviso_nao_reprova(tmp_path: Path) -> None:
    """Imita os logs coletados com o Coordenador fora depois de decidir: Voto SIM sem Decisao.

    O Coordenador gravou a Decisao (COMMIT), mas os Participantes só têm Voto SIM
    durável, porque a DECISAO não chegou. A verificação deve sair 0, com aviso e
    sem reprovação.
    """
    logs = tmp_path / "cenario_2pc_crash-coord_logs"
    # Log_Duravel do Coordenador: decidiu COMMIT.
    escrever_jsonl(
        logs / "coordenador" / "dados" / "coordenador.log",
        [{"transacao": "tx1", "fase": "decisao", "valor": "COMMIT", "no": 1}],
    )
    # Logs_Duraveis dos Participantes: votaram SIM, sem Decisao (em dúvida).
    for pid in (11, 12, 13):
        escrever_jsonl(
            logs / f"participante-{pid - 10}" / "dados" / f"participante-{pid}.log",
            [{"transacao": "tx1", "fase": "voto", "valor": "SIM", "no": pid}],
        )

    saida: list[str] = []
    codigo = verificar.verificar([logs], imprimir=saida.append)
    assert codigo == 0
    texto = "\n".join(saida)
    assert "aviso" in texto.lower()
    assert "Estado_Em_Duvida" in texto


def test_atomicidade_nao_reprova_quando_so_o_coordenador_decidiu() -> None:
    """A atomicidade não reprova com o Coordenador fora depois de decidir: só ele tem Decisao.

    Os Participantes ficam sem nenhuma Decisao durável. Logo não há COMMIT e ABORT
    coexistindo, o invariante 4 passa, e o único achado é o aviso de dúvida do
    invariante 5.
    """
    duraveis = [
        inv.EntradaDuravel("tx1", "decisao", "COMMIT", 1),  # só o Coordenador
        inv.EntradaDuravel("tx1", "voto", "SIM", 11),
        inv.EntradaDuravel("tx1", "voto", "SIM", 12),
        inv.EntradaDuravel("tx1", "voto", "SIM", 13),
    ]
    d = inv.Dados(duraveis=tuple(duraveis))
    v4 = inv.invariante_04_atomicidade(d)
    v5 = inv.invariante_05_durabilidade(d)
    assert v4.ok  # atomicidade intacta
    assert not v5.falhas  # não reprova
    assert v5.avisos  # mas avisa do Estado_Em_Duvida


# --------------------------------------------------------------------------- #
# Sem Docker: coleta dos logs e conferência do desfecho.
# --------------------------------------------------------------------------- #


def test_coleta_do_2pc_copia_cada_volume_uma_vez(tmp_path: Path, monkeypatch) -> None:
    copias: list[tuple[str, str, Path]] = []
    monkeypatch.setattr(
        conteineres,
        "copiar_de_container",
        lambda servico, origem, destino, _arquivos: copias.append((servico, origem, destino)),
    )
    conteineres.coletar_logs("2pc", conteineres.SERVICOS_2PC, tmp_path, [])
    assert copias == [(s, "/dados/.", tmp_path / s) for s in conteineres.SERVICOS_2PC]


def test_copia_de_log_que_falha_levanta_com_o_comando_de_limpeza(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        conteineres,
        "compose",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no such container"),
    )
    with pytest.raises(RuntimeError, match="docker compose down -v --remove-orphans"):
        conteineres.coletar_logs("anel", conteineres.SERVICOS_ANEL, tmp_path, [])


def _compose_registrado(chamadas: list[tuple[str, ...]], falhar_em: str | None = None):
    """Um `compose` falso que anota cada subcomando e falha só em `falhar_em`."""

    def compose(*args, **_kw):
        chamadas.append(args)
        codigo = 1 if args[0] == falhar_em else 0
        return subprocess.CompletedProcess(args, codigo, "", "erro do docker")

    return compose


def test_reiniciar_servico_espera_parar_antes_do_start(monkeypatch) -> None:
    chamadas: list[tuple[str, ...]] = []
    monkeypatch.setattr(conteineres, "compose", _compose_registrado(chamadas))
    conteineres.reiniciar_servico("participante-1", [])
    assert chamadas == [("wait", "participante-1"), ("start", "participante-1")]


def test_esperar_saudavel_espera_sem_recriar_o_conteiner(monkeypatch) -> None:
    chamadas: list[tuple[str, ...]] = []
    monkeypatch.setattr(conteineres, "compose", _compose_registrado(chamadas))
    conteineres.esperar_saudavel("participante-1", [])
    assert chamadas == [("up", "-d", "--wait", "--no-deps", "--no-recreate", "participante-1")]


@pytest.mark.parametrize(
    ("acao", "comando"),
    [
        (lambda: conteineres.reiniciar_servico("participante-1", []), "start"),
        (lambda: conteineres.derrubar_servico("participante-1", []), "kill"),
        (lambda: conteineres.esperar_saudavel("participante-1", []), "up"),
    ],
)
def test_falha_do_compose_vira_erro_acionavel(monkeypatch, acao, comando: str) -> None:
    monkeypatch.setattr(conteineres, "compose", _compose_registrado([], falhar_em=comando))
    with pytest.raises(RuntimeError, match=f"{comando}.*participante-1.*down -v --remove-orphans"):
        acao()


def test_servico_sem_saude_depois_do_start_leva_a_cauda_do_log(monkeypatch) -> None:
    # O down do fim do cenário apaga o contêiner, então a mensagem traz o log dele.
    monkeypatch.setattr(cenario, "esperar_saude", lambda *_a: False)
    monkeypatch.setattr(
        cenario, "compose", lambda *a, **_k: subprocess.CompletedProcess(a, 0, "cauda do log", "")
    )
    with pytest.raises(RuntimeError) as exc:
        cenario._exigir_saude("participante-1", "http://127.0.0.1:8011", "2pc crash-coord-part", [])
    mensagem = str(exc.value)
    assert "python scripts/cenario.py 2pc crash-coord-part" in mensagem
    assert mensagem.endswith("Últimas linhas do participante-1:\ncauda do log")


def _conduzir_crash_coord(
    monkeypatch, modo: str, estado_11_em_duvida: dict | None = None
) -> tuple[list[str], dict]:
    """Conduz `_cenario_2pc` sem Docker e devolve os passos, na ordem, e o registro."""
    passos: list[str] = []
    url_11 = f"http://127.0.0.1:{cenario.PORTAS_PART[11]}/estado"

    def http_json(url: str, metodo: str = "GET", **_kw) -> dict:
        if url.endswith("/transacao"):
            raise urllib.error.URLError("conexão recusada")
        assert url == url_11
        passos.append("estado 11")
        if "coordenador" in passos:
            return {"voto": "SIM", "decisao": "COMMIT", "em_duvida": False}
        return estado_11_em_duvida or {"voto": "SIM", "decisao": None, "em_duvida": True}

    def anotar(rotulo: str, retorno=None):
        def registrar(*args, **_kw):
            passos.append(rotulo.format(*args))
            return retorno

        return registrar

    em_duvida = {pid: {"voto": "SIM", "decisao": None, "em_duvida": True} for pid in (11, 12, 13)}
    monkeypatch.setattr(cenario, "esperar_saude", lambda *_a: True)
    monkeypatch.setattr(cenario, "http_json", http_json)
    monkeypatch.setattr(cenario, "estados_participantes", lambda **_k: em_duvida)
    monkeypatch.setattr(cenario, "derrubar_servico", anotar("kill {0}"))
    monkeypatch.setattr(cenario, "reiniciar_servico", anotar("start {0}"))
    monkeypatch.setattr(cenario, "esperar_saudavel", anotar("saudável {0}"))
    monkeypatch.setattr(
        cenario, "_verificar_com_coordenador_fora", anotar("verificação", (0, ["aviso:"] * 3))
    )
    monkeypatch.setattr(
        cenario,
        "reiniciar_coordenador",
        anotar("coordenador", (1.0, dict.fromkeys((11, 12, 13), "COMMIT"))),
    )
    monkeypatch.setattr(
        cenario, "esperar_conclusao", anotar("conclusão", {"decisao": "COMMIT", "concluido": True})
    )
    return passos, cenario._cenario_2pc(modo, [], io.StringIO())


def test_crash_coord_part_reinicia_o_participante_antes_do_coordenador(monkeypatch) -> None:
    """O 11 volta em dúvida antes da verificação com o Coordenador fora, e com COMMIT no fim."""
    passos, registro = _conduzir_crash_coord(monkeypatch, "crash-coord-part")
    assert passos == [
        "kill participante-1",
        "start participante-1",
        "estado 11",
        "saudável participante-1",
        "verificação",
        "coordenador",
        "conclusão",
        "kill participante-1",
        "start participante-1",
        "estado 11",
    ]
    assert cenario._divergencias(registro) == []


def test_crash_coord_nao_reinicia_o_participante(monkeypatch) -> None:
    passos, registro = _conduzir_crash_coord(monkeypatch, "crash-coord")
    assert passos == ["verificação", "coordenador", "conclusão"]
    assert registro["falha_injetada"] == (
        "processo do Coordenador morre depois de gravar a Decisao "
        "(DOISPC_CAIR_APOS_DECIDIR) e o contêiner é reiniciado"
    )
    for chave in ("reinicio_em_duvida", "reinicio_apos_decidir"):
        assert chave not in registro
        assert chave not in registro["esperado"]
    assert cenario._divergencias(registro) == []


def test_crash_coord_part_diverge_quando_o_11_volta_fora_da_duvida(monkeypatch) -> None:
    _, registro = _conduzir_crash_coord(
        monkeypatch, "crash-coord-part", {"voto": None, "decisao": None, "em_duvida": False}
    )
    divergencias = cenario._divergencias(registro)
    assert len(divergencias) == 1
    assert divergencias[0].startswith("reinicio_em_duvida")


def _logs_de_commit(_algoritmo, _servicos, destino: Path, _arquivos) -> None:
    """Logs de um COMMIT com Voto SIM de todos, que verificam os invariantes 04, 06 e 07."""
    escrever_jsonl(
        destino / "coordenador" / "coordenador.log",
        [
            {"transacao": "tx1", "fase": "inicio", "valor": "WAIT", "no": 1},
            {"transacao": "tx1", "fase": "decisao", "valor": "COMMIT", "no": 1},
        ],
    )
    for pid in (11, 12, 13):
        escrever_jsonl(
            destino / f"participante-{pid - 10}" / f"participante-{pid}.log",
            [
                {"transacao": "tx1", "fase": "voto", "valor": "SIM", "no": pid},
                {"transacao": "tx1", "fase": "decisao", "valor": "COMMIT", "no": pid},
            ],
        )


def _cenario_sem_docker(monkeypatch, coletar, decisao: str = "COMMIT") -> None:
    """Troca o compose, a coleta e a condução de um `2pc commit` que termina em `decisao`."""
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    monkeypatch.setattr(
        cenario, "compose", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    )
    monkeypatch.setattr(cenario, "coletar_logs", coletar)
    monkeypatch.setattr(cenario, "_coletar_stdout", lambda *a: None)
    monkeypatch.setattr(
        cenario,
        "_cenario_2pc",
        lambda *a: {"cenario": "2pc_commit", "decisao": decisao, "esperado": {"decisao": "COMMIT"}},
    )


def test_cenario_sem_log_reprova(tmp_path: Path, monkeypatch) -> None:
    # Sem log, nenhum invariante tem caso, e os do modo são exigidos.
    _cenario_sem_docker(monkeypatch, lambda *a: None)
    saida = io.StringIO()
    assert cenario.executar_cenario("2pc", "commit", tmp_path, saida=saida, erro=io.StringIO()) == 1
    assert "não tem log" in saida.getvalue()
    assert _registro(tmp_path, "2pc", "commit")["verificacao"]["ok"] is False


@pytest.mark.parametrize(("decisao", "codigo"), [("COMMIT", 0), ("ABORT", 1)])
def test_cenario_sai_1_quando_o_desfecho_difere_do_esperado(
    tmp_path: Path, monkeypatch, decisao: str, codigo: int
) -> None:
    # Um `2pc commit` que termina em ABORT passa nos invariantes de safety, e só
    # a conferência do desfecho o pega. O compose e a condução são substituídos.
    _cenario_sem_docker(monkeypatch, _logs_de_commit, decisao)
    erro = io.StringIO()
    assert cenario.executar_cenario("2pc", "commit", tmp_path, saida=io.StringIO(), erro=erro) == codigo
    reg = _registro(tmp_path, "2pc", "commit")
    assert reg["desfecho"]["ok"] is (codigo == 0)
    assert ("difere do esperado" in erro.getvalue()) is (codigo == 1)


# --------------------------------------------------------------------------- #
# Sem Docker: caminho de degradação (código 2).
# --------------------------------------------------------------------------- #


def test_cenario_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: False)
    codigo = cenario.executar_cenario(
        "2pc", "crash-coord", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 2


def test_demo_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: False)
    codigo = demo.executar_demo(tmp_path, saida=io.StringIO(), erro=io.StringIO())
    assert codigo == 2


def test_demo_cenarios_de_falha_sao_os_dois_exigidos() -> None:
    # A demo encadeia exatamente os dois cenários de falha do enunciado.
    assert demo.CENARIOS_FALHA == (("anel", "falha"), ("2pc", "crash-coord"))


# --------------------------------------------------------------------------- #
# Com Docker (slow): cada cenário roda de ponta a ponta e verifica.
# --------------------------------------------------------------------------- #

pularsem_docker = pytest.mark.skipif(
    not conteineres.docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; cenário por contêiner pulado",
)


@pytest.mark.slow
@pularsem_docker
def test_anel_falha_e2e_verifica(tmp_path: Path) -> None:
    """`anel falha`: morte do Líder → reeleição para o maior vivo, e os invariantes passam."""
    codigo = cenario.executar_cenario("anel", "falha", tmp_path, semente=0)
    assert codigo == 0, "verificação deveria passar no cenário anel/falha"
    reg = _registro(tmp_path, "anel", "falha")
    assert reg["semente"] == 0
    assert reg["desfecho"]["ok"] is True
    assert reg["lideres_pos_falha"] == {str(i): 4 for i in (1, 2, 3, 4)}
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_anel_reinicio_e2e_reintegra(tmp_path: Path) -> None:
    """`anel reinicio`: o 5 volta sem memória e a terceira eleição o reintegra como Líder."""
    codigo = cenario.executar_cenario("anel", "reinicio", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "anel", "reinicio")
    assert reg["lideres_pos_falha"] == {str(i): 4 for i in (1, 2, 3, 4)}
    assert reg["estado_5_apos_reinicio"] == {"lider": None, "rodadas_concluidas": []}
    assert reg["lideres_pos_reinicio"] == {str(i): 5 for i in range(1, 6)}
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    assert any("1. Unicidade" in l and "3 rodada(s)" in l for l in reg["verificacao"]["relatorio"])


@pytest.mark.slow
@pularsem_docker
def test_2pc_commit_e2e_verifica(tmp_path: Path) -> None:
    """`2pc commit`: todos SIM → COMMIT em todos, e atomicidade e durabilidade passam."""
    codigo = cenario.executar_cenario("2pc", "commit", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "commit")
    assert reg["semente"] == 0
    assert reg["decisao"] == "COMMIT"
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_abort_e2e_verifica(tmp_path: Path) -> None:
    """`2pc abort`: um NAO → ABORT atômico em todos, e os invariantes passam."""
    codigo = cenario.executar_cenario("2pc", "abort", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "abort")
    assert reg["decisao"] == "ABORT"
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_2pc_crash_coord_e2e_recupera(tmp_path: Path) -> None:
    """`2pc crash-coord`: dúvida com o Coordenador fora, COMMIT em todos depois do reinício."""
    codigo = cenario.executar_cenario("2pc", "crash-coord", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "crash-coord")
    assert reg["semente"] == 0
    assert reg["participantes_em_duvida"] == [11, 12, 13]
    # Com o Coordenador fora, os três estão em dúvida: três avisos e nenhuma reprovação.
    assert reg["verificacao_com_coordenador_fora"]["codigo"] == 0
    assert reg["avisos_com_coordenador_fora"] == 3
    assert reg["decisao"] == "COMMIT"
    assert reg["concluido"] is True
    assert set(reg["decisoes_participantes"].values()) == {"COMMIT"}
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # Ninguém termina em dúvida, então o invariante 5 não avisa nada.
    assert not any("aviso:" in l for l in reg["verificacao"]["relatorio"])


@pytest.mark.slow
@pularsem_docker
def test_2pc_crash_coord_part_e2e_recupera(tmp_path: Path) -> None:
    """`2pc crash-coord-part`: o 11 volta em dúvida com o Coordenador fora e depois com COMMIT."""
    codigo = cenario.executar_cenario("2pc", "crash-coord-part", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "crash-coord-part")
    assert reg["participantes_em_duvida"] == [11, 12, 13]
    assert reg["verificacao_com_coordenador_fora"]["codigo"] == 0
    assert reg["avisos_com_coordenador_fora"] == 3
    assert reg["reinicio_em_duvida"] == {"voto": "SIM", "decisao": None, "em_duvida": True}
    assert reg["reinicio_apos_decidir"] == {"voto": "SIM", "decisao": "COMMIT", "em_duvida": False}
    assert reg["decisao"] == "COMMIT"
    assert reg["concluido"] is True
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # O contêiner do 11 nunca é recriado: o log dele tem uma recuperacao por reinício.
    jsonl = tmp_path / "cenario_2pc_crash-coord-part_logs" / "participante-1" / "logs"
    recuperacoes = [ev for ev in inv.carregar_eventos(jsonl) if ev.tipo == inv.EV_RECUPERACAO]
    assert [ev.campos["decisao"] for ev in recuperacoes] == [None, "COMMIT"]
    # O 05 confere a recuperação do Coordenador e a segunda do 11, a que traz a Decisao.
    relatorio = reg["verificacao"]["relatorio"]
    assert any("as 2 recuperação(ões)" in l for l in relatorio)
    assert not any("aviso:" in l for l in relatorio)


@pytest.mark.slow
@pularsem_docker
def test_2pc_particao_e2e_aborta_e_termina(tmp_path: Path) -> None:
    """`2pc particao`: ABORT no prazo de Votos com o 11 isolado, e ABORT no 11 depois da cura."""
    codigo = cenario.executar_cenario("2pc", "particao", tmp_path, semente=0)
    assert codigo == 0
    reg = _registro(tmp_path, "2pc", "particao")
    assert reg["decisao"] == "ABORT"
    assert reg["decisoes_durante_particao"] == {"11": None, "12": "ABORT", "13": "ABORT"}
    assert reg["concluido_durante_particao"] is False
    assert reg["decisoes_participantes"] == {"11": "ABORT", "12": "ABORT", "13": "ABORT"}
    assert reg["concluido"] is True
    # Os três votariam SIM, então o ABORT veio do prazo de coleta de Votos.
    log = tmp_path / "cenario_2pc_particao_logs" / "coordenador" / "logs" / "doispc-coordenador-1.jsonl"
    eventos = [json.loads(l)["tipo"] for l in log.read_text(encoding="utf-8").splitlines()]
    assert "timeout_votos" in eventos
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
