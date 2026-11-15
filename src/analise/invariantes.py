"""Invariantes de correção sobre os logs do T2, um invariante por função.

Cada invariante é uma função de `Dados` para `Veredito`: a função lê o que precisa
dos logs já carregados e devolve um veredito que **passa**, **reprova** ou
**avisa**. A distinção central do T2 é decisão de projeto. Uma reprovação
indica violação de uma propriedade de *safety*, e um aviso registra um
fenômeno esperado — como o Estado_Em_Duvida bloqueante do 2PC quando o
Coordenador cai — e **não** altera o código de saída. `scripts/verificar.py`
sai com código diferente de zero só quando algum invariante reprova.

Os invariantes cobrem as propriedades declaradas em `docs/modelo-e-correcao.md`:

Anel
  1. **Unicidade do Líder por rodada** (*safety*): no máximo um Líder por
     Rodada_de_Eleicao concluída, identificada pelo id único da rodada. REPROVA.
  2. **Convergência dos vivos** (*liveness*): todos os Nós vivos que reportam um
     Líder ao fim de uma rodada reportam o mesmo. REPROVA se divergirem.
  3. **O eleito é o maior id vivo** (*correctness*): o Líder anunciado numa
     rodada concluída é o maior id entre os Nós vivos. REPROVA.

2PC
  4. **Atomicidade** (*safety*): nenhum Nó decide COMMIT enquanto outro decide
     ABORT para a mesma transação. REPROVA.
  5. **Durabilidade** (*safety*): a Decisao gravada no Log_Duravel sobrevive ao
     reinício — a Decisao recuperada é igual à de antes. REPROVA se reverter.
     Um Participante que votou SIM mas não tem Decisao durável (Estado_Em_Duvida
     por crash do Coordenador) vira **aviso**, não reprovação.
  6. **Validade** (*safety*): COMMIT só quando todos os Participantes votaram
     SIM. REPROVA.

Este módulo é **autocontido** de propósito: lê os logs estruturados (JSONL) e os
Logs_Duraveis direto com a biblioteca padrão (`json`), sem depender dos módulos
de carga/estatística da camada de análise. Isso o mantém verificável mesmo
quando a frente de análise ainda está em construção. Esta camada lê dados, não
executa Réplica nem sobe contêiner.

As tolerâncias não usam nenhum número redondo
escolhido para caber nos dados. As propriedades aqui são de *safety* exata sobre
eventos discretos (um Líder, uma Decisao), então a tolerância natural é zero
desvio — qualquer violação é um defeito, não ruído amostral. A única folga
admitida é a distinção reprova/aviso, justificada caso a caso
na docstring de cada invariante e em `docs/invariantes.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.anel.maquina import TIPO_LIDER
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    FASE_DECISAO,
    FASE_VOTO,
    VOTO_SIM,
)

# --------------------------------------------------------------------------- #
# Tipos de evento, espelhando os produtores dos logs.
# --------------------------------------------------------------------------- #

# Tipos de evento do log estruturado do Anel (ver src/anel/no.py).
EV_INICIOU_ELEICAO = "iniciou_eleicao"
EV_RECEBEU = "recebeu"
EV_REPASSOU = "repassou"
EV_SUCESSOR_FALHO = "sucessor_falho"

# Tipos de evento do log estruturado do 2PC (ver src/doispc/executor.py e
# coordenador.py/participante.py).
EV_RECUPERACAO = "recuperacao"
EV_INICIA_TRANSACAO = "inicia_transacao"

# Recorte do relatório: lista no máximo algumas ocorrências por
# invariante, para o relatório caber na tela.
MAX_OCORRENCIAS_LISTADAS: int = 5


# --------------------------------------------------------------------------- #
# Modelos de dados dos logs (imutáveis), no estilo frozen do projeto.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EventoLog:
    """Uma linha do Log_Estruturado JSONL: `{ts, no, tipo, ...campos}`.

    `ts`, `no` e `tipo` são as chaves reservadas que `src/comum/log.py` sempre
    grava; `campos` guarda o restante da linha (os campos específicos do evento,
    como `corpo`, `transacao`, `fase`, `valor`).
    """

    ts: float
    no: int
    tipo: str
    campos: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def de_json(cls, dados: Mapping[str, Any]) -> "EventoLog":
        """Reconstrói um `EventoLog` de uma linha desserializada, tolerante a ruído."""
        reservadas = {"ts", "no", "tipo"}
        campos = {k: v for k, v in dados.items() if k not in reservadas}
        return cls(
            ts=float(dados.get("ts", 0.0)),
            no=int(dados["no"]),
            tipo=str(dados["tipo"]),
            campos=campos,
        )


@dataclass(frozen=True)
class EntradaDuravel:
    """Uma entrada do Log_Duravel: `{transacao, fase, valor, no}`.

    Espelha `EntradaLog.para_json` de `src/doispc/log_duravel.py`. É a unidade
    sobre a qual os invariantes de atomicidade e durabilidade raciocinam.
    """

    transacao: str
    fase: str
    valor: str
    no: int

    @classmethod
    def de_json(cls, dados: Mapping[str, Any]) -> "EntradaDuravel":
        return cls(
            transacao=str(dados["transacao"]),
            fase=str(dados["fase"]),
            valor=str(dados["valor"]),
            no=int(dados["no"]),
        )


@dataclass(frozen=True)
class Dados:
    """Tudo que os invariantes leem, já carregado do disco.

    `eventos` são as linhas do Log_Estruturado de todos os Nós, na ordem em que
    foram lidas. `duraveis` são as entradas de todos os Logs_Duraveis do 2PC.
    """

    eventos: tuple[EventoLog, ...] = ()
    duraveis: tuple[EntradaDuravel, ...] = ()


@dataclass(frozen=True)
class Veredito:
    """O resultado de um invariante. Avisos não derrubam `ok`."""

    numero: int
    titulo: str
    ok: bool
    verificados: int
    detalhe: str
    falhas: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Leitura dos logs com a biblioteca padrão (autocontido, sem depender da análise).
# --------------------------------------------------------------------------- #


def carregar_eventos(*caminhos: Path) -> tuple[EventoLog, ...]:
    """Lê linhas JSONL de Log_Estruturado de um ou mais arquivos ou diretórios.

    Um caminho pode ser um arquivo `.jsonl`/`.log` ou um diretório que os contém
    (varrido recursivamente). Linhas vazias ou corrompidas são ignoradas, no
    mesmo espírito tolerante a escrita parcial do `LogDuravelArquivo`: uma linha
    só conta quando é um objeto JSON com as chaves reservadas.
    """
    eventos: list[EventoLog] = []
    for arquivo in _expandir_arquivos(caminhos):
        for registro in _ler_jsonl(arquivo):
            if "no" in registro and "tipo" in registro:
                try:
                    eventos.append(EventoLog.de_json(registro))
                except (KeyError, ValueError, TypeError):
                    continue
    return tuple(eventos)


def carregar_duraveis(*caminhos: Path) -> tuple[EntradaDuravel, ...]:
    """Lê entradas de Log_Duravel de arquivos ou diretórios.

    Uma linha é entrada durável quando tem exatamente as chaves de
    `EntradaLog.para_json`. O evento `grava_log` do Log_Estruturado repete
    essas chaves junto com `ts` e `tipo`, e por isso não conta como durável.
    """
    entradas: list[EntradaDuravel] = []
    for arquivo in _expandir_arquivos(caminhos):
        for registro in _ler_jsonl(arquivo):
            if _parece_entrada_duravel(registro):
                try:
                    entradas.append(EntradaDuravel.de_json(registro))
                except (KeyError, ValueError, TypeError):
                    continue
    return tuple(entradas)


def _parece_entrada_duravel(registro: Mapping[str, Any]) -> bool:
    """Verdadeiro quando o registro é uma linha de Log_Duravel, e não um evento."""
    return registro.keys() == {"transacao", "fase", "valor", "no"}


def _expandir_arquivos(caminhos: Iterable[Path]) -> list[Path]:
    """Expande arquivos e diretórios numa lista ordenada de arquivos de log.

    Diretórios são varridos recursivamente por `.log`, `.jsonl` e `.ndjson`.
    Caminhos inexistentes são ignorados silenciosamente, para que verificar um
    diretório `results/` ainda vazio não quebre — apenas não há o que verificar.
    """
    arquivos: list[Path] = []
    for caminho in caminhos:
        caminho = Path(caminho)
        if caminho.is_dir():
            for padrao in ("*.log", "*.jsonl", "*.ndjson"):
                arquivos.extend(sorted(caminho.rglob(padrao)))
        elif caminho.is_file():
            arquivos.append(caminho)
    # Remove duplicatas preservando a ordem (um arquivo pode casar dois padrões).
    vistos: set[Path] = set()
    unicos: list[Path] = []
    for arquivo in arquivos:
        if arquivo not in vistos:
            vistos.add(arquivo)
            unicos.append(arquivo)
    return unicos


def _ler_jsonl(arquivo: Path) -> list[dict[str, Any]]:
    """Lê um arquivo JSONL, devolvendo os objetos; ignora linhas vazias/corrompidas."""
    registros: list[dict[str, Any]] = []
    try:
        texto = arquivo.read_text(encoding="utf-8")
    except OSError:
        return registros
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            dados = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(dados, dict):
            registros.append(dados)
    return registros


# --------------------------------------------------------------------------- #
# Montagem do veredito.
# --------------------------------------------------------------------------- #


def veredito(
    numero: int,
    titulo: str,
    verificados: int,
    falhas: list[str],
    detalhe: str,
    avisos: list[str] | None = None,
) -> Veredito:
    """Monta o veredito; reprova quando nada foi verificado.

    "Nenhum caso a verificar" é tratado como reprovação: um invariante
    que não encontrou dado algum não demonstrou a propriedade, e silenciar isso
    daria uma falsa sensação de correção. O comando de verificação, porém, decide
    o que fazer com a ausência de dados (ver `scripts/verificar.py`), que pode
    pular um invariante quando o algoritmo correspondente não foi exercitado.
    """
    if verificados == 0:
        return Veredito(
            numero, titulo, False, 0,
            "nenhum caso a verificar nos dados fornecidos",
            avisos=tuple(_recortar(avisos or [])),
        )
    return Veredito(
        numero=numero,
        titulo=titulo,
        ok=not falhas,
        verificados=verificados,
        detalhe=detalhe if not falhas else f"{len(falhas)} caso(s) reprovado(s)",
        falhas=tuple(_recortar(falhas)),
        avisos=tuple(_recortar(avisos or [])),
    )


def _recortar(mensagens: list[str]) -> list[str]:
    """Corta a lista no limite do relatório, dizendo quantas ficaram de fora."""
    if len(mensagens) <= MAX_OCORRENCIAS_LISTADAS:
        return mensagens
    restantes = len(mensagens) - MAX_OCORRENCIAS_LISTADAS
    return mensagens[:MAX_OCORRENCIAS_LISTADAS] + [f"e mais {restantes} caso(s)"]


# --------------------------------------------------------------------------- #
# Extração de rodadas do Anel a partir dos eventos.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AnuncioLider:
    """Um anúncio de Líder observado num evento `recebeu` de Mensagem_Lider.

    `rodada` é o id único da Rodada_de_Eleicao, gerado por quem a iniciou.
    `lider` é o id anunciado, e `no` é o Nó que observou o anúncio. Vários Nós
    observam o mesmo anúncio ao longo da circulação da Mensagem_Lider; os
    invariantes agregam por rodada.
    """

    rodada: str
    lider: int
    no: int


def _anuncios_de_lider(eventos: Sequence[EventoLog]) -> list[AnuncioLider]:
    """Extrai os anúncios de Líder dos eventos `recebeu` de Mensagem_Lider.

    Um Nó registra `recebeu` com `msg_tipo="lider"` e um `corpo` que carrega
    `rodada` e `lider` (ver `src/anel/no.py` e `src/anel/maquina.py`). Esse é
    o sinal observável de que um Nó tomou ciência do Líder de uma rodada, do qual
    os invariantes 1, 2 e 3 derivam suas conclusões.
    """
    anuncios: list[AnuncioLider] = []
    for ev in eventos:
        if ev.tipo != EV_RECEBEU:
            continue
        if ev.campos.get("msg_tipo") != TIPO_LIDER:
            continue
        corpo = ev.campos.get("corpo") or {}
        if "rodada" not in corpo or "lider" not in corpo:
            continue
        try:
            anuncios.append(
                AnuncioLider(
                    rodada=str(corpo["rodada"]),
                    lider=int(corpo["lider"]),
                    no=int(ev.no),
                )
            )
        except (TypeError, ValueError):
            continue
    return anuncios


def _nos_do_anel(eventos: Sequence[EventoLog]) -> frozenset[int]:
    """Todos os ids de Nó que emitiram algum evento do Anel."""
    anel_evts = {
        EV_INICIOU_ELEICAO, EV_RECEBEU, EV_REPASSOU, EV_SUCESSOR_FALHO,
    }
    return frozenset(ev.no for ev in eventos if ev.tipo in anel_evts)


def _mortos_observados(eventos: Sequence[EventoLog], rodada: str) -> frozenset[int]:
    """Ids saltados na `rodada`: o alvo de cada evento `sucessor_falho` dela.

    Um Nó saltado numa rodada pode estar vivo na seguinte, então a morte não
    vale para o log inteiro.
    """
    mortos: set[int] = set()
    for ev in eventos:
        if ev.tipo != EV_SUCESSOR_FALHO or ev.campos.get("rodada") != rodada:
            continue
        sucessor = ev.campos.get("sucessor")
        if sucessor is not None:
            try:
                mortos.add(int(sucessor))
            except (TypeError, ValueError):
                continue
    return frozenset(mortos)


def _vivos_na_rodada(d: Dados, rodada: str) -> frozenset[int]:
    """Os Nós vivos na rodada: os do Anel menos os saltados nela.

    Um Nó morto aparece como `sucessor_falho` no log de quem o contornou.
    """
    return _nos_do_anel(d.eventos) - _mortos_observados(d.eventos, rodada)


# --------------------------------------------------------------------------- #
# Invariantes do Anel.
# --------------------------------------------------------------------------- #


def invariante_01_lider_unico(d: Dados) -> Veredito:
    """No máximo um Líder por Rodada_de_Eleicao concluída (safety). REPROVA.

    Cada rodada tem um id único, gerado por quem a inicia. Agrupamos os anúncios
    por rodada e exigimos que todos concordem num único `lider`. Dois valores
    distintos de Líder na mesma rodada são uma violação de safety, como a que
    uma eleição atrasada produziria ao fechar de novo uma rodada já concluída. A
    tolerância é zero: não há margem amostral numa igualdade de inteiros.
    """
    anuncios = _anuncios_de_lider(d.eventos)
    por_rodada: dict[str, set[int]] = {}
    for a in anuncios:
        por_rodada.setdefault(a.rodada, set()).add(a.lider)

    falhas: list[str] = []
    for rodada in sorted(por_rodada):
        lideres = por_rodada[rodada]
        if len(lideres) > 1:
            falhas.append(
                f"rodada {rodada}: {len(lideres)} Líderes distintos {sorted(lideres)}"
            )
    return veredito(
        1, "Unicidade do Líder por rodada (Anel)", len(por_rodada), falhas,
        f"cada uma das {len(por_rodada)} rodada(s) concluída(s) anuncia um único Líder",
    )


def invariante_02_convergencia(d: Dados) -> Veredito:
    """Todos os Nós vivos convergem para o mesmo Líder (liveness/safety). REPROVA.

    Por rodada, todos os Nós que observaram o anúncio devem reportar o mesmo
    `lider`, e esse conjunto de observadores deve cobrir os Nós vivos da rodada —
    a condição de convergência: ao fim da rodada, todos os vivos conhecem o
    mesmo Líder. Divergência entre observadores reprova; um vivo que
    jamais observou o anúncio também reprova, pois a rodada não convergiu para
    ele. A tolerância é zero: convergência é um predicado exato.
    """
    anuncios = _anuncios_de_lider(d.eventos)
    por_rodada: dict[str, list[AnuncioLider]] = {}
    for a in anuncios:
        por_rodada.setdefault(a.rodada, []).append(a)

    falhas: list[str] = []
    for rodada in sorted(por_rodada):
        grupo = por_rodada[rodada]
        lideres = {a.lider for a in grupo}
        observadores = {a.no for a in grupo}
        if len(lideres) > 1:
            falhas.append(
                f"rodada {rodada}: observadores reportam Líderes diferentes "
                f"{sorted(lideres)}"
            )
            continue
        vivos = _vivos_na_rodada(d, rodada)
        faltam = vivos - observadores
        if vivos and faltam:
            falhas.append(
                f"rodada {rodada}: Nó(s) vivo(s) {sorted(faltam)} não "
                f"conheceram o Líder {next(iter(lideres))}"
            )
    return veredito(
        2, "Convergência dos Nós vivos (Anel)", len(por_rodada), falhas,
        f"em cada uma das {len(por_rodada)} rodada(s), todos os Nós vivos "
        "reportam o mesmo Líder",
    )


def invariante_03_maior_id_vivo(d: Dados) -> Veredito:
    """O Líder eleito é o maior id vivo (correctness). REPROVA.

    Para cada rodada concluída, o `lider` anunciado deve ser igual ao maior id
    entre os Nós vivos daquela rodada. É a definição de Líder do glossário e a
    propriedade de safety da eleição: ela determina o maior id vivo,
    independentemente de quem iniciou. Tolerância zero, de novo por ser igualdade
    de inteiros — qualquer discrepância é um defeito, não flutuação.
    """
    anuncios = _anuncios_de_lider(d.eventos)
    por_rodada: dict[str, set[int]] = {}
    for a in anuncios:
        por_rodada.setdefault(a.rodada, set()).add(a.lider)

    falhas: list[str] = []
    verificados = 0
    for rodada in sorted(por_rodada):
        lideres = por_rodada[rodada]
        if len(lideres) != 1:
            # A unicidade é do invariante 1; aqui só checamos rodadas bem-formadas.
            continue
        vivos = _vivos_na_rodada(d, rodada)
        if not vivos:
            continue
        verificados += 1
        eleito = next(iter(lideres))
        esperado = max(vivos)
        if eleito != esperado:
            falhas.append(
                f"rodada {rodada}: Líder eleito {eleito}, mas o maior id vivo "
                f"é {esperado} (vivos {sorted(vivos)})"
            )
    return veredito(
        3, "Líder é o maior id vivo (Anel)", verificados, falhas,
        f"o Líder de cada uma das {verificados} rodada(s) é o maior id vivo",
    )


# --------------------------------------------------------------------------- #
# Invariantes do 2PC.
# --------------------------------------------------------------------------- #


def _decisoes_por_transacao(d: Dados) -> dict[str, dict[int, str]]:
    """Mapeia transação -> {no: primeira Decisao durável desse Nó}.

    A primeira Decisao por (transacao, no) é a que vale: uma vez decidida, a
    transação não reverte, então entradas posteriores de Decisao
    do mesmo Nó são ignoradas, reproduzindo a redução de `log_duravel._reduzir`.
    """
    decisoes: dict[str, dict[int, str]] = {}
    for e in d.duraveis:
        if e.fase != FASE_DECISAO:
            continue
        por_no = decisoes.setdefault(e.transacao, {})
        if e.no not in por_no:  # primeira Decisao do Nó vence
            por_no[e.no] = e.valor
    return decisoes


def _votos_por_transacao(d: Dados) -> dict[str, dict[int, str]]:
    """Mapeia transação -> {no: último Voto durável desse Nó}."""
    votos: dict[str, dict[int, str]] = {}
    for e in d.duraveis:
        if e.fase != FASE_VOTO:
            continue
        votos.setdefault(e.transacao, {})[e.no] = e.valor  # último Voto vence
    return votos


def invariante_04_atomicidade(d: Dados) -> Veredito:
    """Atomicidade do 2PC: nenhum COMMIT enquanto outro ABORT na mesma transação.

    Safety: numa dada transação, todos os Nós que gravaram uma
    Decisao concordam. Lemos a Decisao durável de cada Nó (Coordenador e
    Participantes) a partir dos Logs_Duraveis e reprovamos se, para alguma
    transação, aparecem COMMIT e ABORT ao mesmo tempo. A tolerância é zero: a
    atomicidade é uma propriedade de tudo-ou-nada, sem gradação.
    """
    decisoes = _decisoes_por_transacao(d)
    falhas: list[str] = []
    verificados = 0
    for transacao in sorted(decisoes):
        por_no = decisoes[transacao]
        verificados += 1
        if len(set(por_no.values())) > 1:
            commit = sorted(n for n, v in por_no.items() if v == DECISAO_COMMIT)
            abort = sorted(n for n, v in por_no.items() if v == DECISAO_ABORT)
            falhas.append(
                f"transação {transacao}: COMMIT em {commit} e ABORT em {abort}"
            )
    return veredito(
        4, "Atomicidade do 2PC", verificados, falhas,
        f"as {verificados} transação(ões) com Decisao durável são atômicas "
        "(todos COMMIT ou todos ABORT)",
    )


def invariante_05_durabilidade(d: Dados) -> Veredito:
    """Durabilidade do 2PC: a Decisao persiste após reinício (safety). REPROVA se reverter.

    Dois sinais no log sustentam esta propriedade:

    - Se um Nó registra uma `recuperacao` (releu o Log_Duravel após reinício), a
      `decisao` recuperada deve bater com a Decisao durável daquele Nó para a
      transação. Divergência é reversão após reinício e REPROVA (
      6.8).
    - Um Participante que gravou Voto SIM mas não tem Decisao durável está em
      Estado_Em_Duvida — o bloqueio esperado do 2PC quando o Coordenador cai
      antes de difundir a Decisao. Isso vira **aviso**, nunca
      reprovação: bloquear é comportamento correto do algoritmo,
      não defeito de durabilidade. A reprovação fica reservada à reversão de uma
      Decisao já registrada.

    Só a `recuperacao` conta como caso verificado. Sem reinício nos logs, o
    invariante fica em lacuna, e os avisos de dúvida aparecem mesmo assim.

    Tolerância: zero para a reversão (igualdade de Decisao); o Estado_Em_Duvida é
    classificado como aviso por decisão de projeto, não por folga
    numérica.
    """
    decisoes = _decisoes_por_transacao(d)
    votos = _votos_por_transacao(d)

    falhas: list[str] = []
    avisos: list[str] = []
    verificados = 0

    # Reversão após reinício: a `recuperacao` tem de reproduzir a Decisao durável.
    for ev in d.eventos:
        if ev.tipo != EV_RECUPERACAO:
            continue
        transacao = ev.campos.get("transacao")
        recuperada = ev.campos.get("decisao")
        if transacao is None or recuperada is None:
            continue  # recuperação de um Nó ainda em dúvida: coberto pelo aviso abaixo
        verificados += 1
        gravada = decisoes.get(str(transacao), {}).get(int(ev.no))
        if gravada is not None and str(recuperada) != gravada:
            falhas.append(
                f"transação {transacao}, nó {ev.no}: recuperou {recuperada}, "
                f"mas o Log_Duravel tinha {gravada}"
            )

    # Estado_Em_Duvida: votou SIM e não tem Decisao durável -> aviso (não reprova).
    for transacao in sorted(votos):
        decisoes_tx = decisoes.get(transacao, {})
        for no in sorted(votos[transacao]):
            if votos[transacao][no] != VOTO_SIM:
                continue
            if no not in decisoes_tx:
                avisos.append(
                    f"transação {transacao}, nó {no}: votou SIM e segue sem "
                    "Decisao durável (Estado_Em_Duvida, bloqueio esperado do 2PC)"
                )

    return veredito(
        5, "Durabilidade da Decisao do 2PC", verificados, falhas,
        f"as {verificados} recuperação(ões) após reinício reproduzem a Decisao registrada",
        avisos,
    )


def _participantes_por_transacao(d: Dados) -> dict[str, set[int]]:
    """Mapeia transação -> Participantes que o Coordenador listou em `inicia_transacao`."""
    participantes: dict[str, set[int]] = {}
    for ev in d.eventos:
        if ev.tipo == EV_INICIA_TRANSACAO and "transacao" in ev.campos:
            participantes.setdefault(str(ev.campos["transacao"]), set()).update(
                int(p) for p in ev.campos.get("participantes", [])
            )
    return participantes


def invariante_06_validade(d: Dados) -> Veredito:
    """Validade do 2PC: COMMIT só quando todos os Participantes votaram SIM. REPROVA.

    A atomicidade sozinha aprova um sistema que sempre aborta, ou que confirma
    sem ouvir um Participante. Os Participantes de uma transação são os que o
    Coordenador listou no Log_Estruturado e os que gravaram Voto, e cada um
    precisa de um Voto SIM no próprio Log_Duravel. ABORT é sempre válido, então
    só as transações com COMMIT contam como casos.
    """
    decisoes = _decisoes_por_transacao(d)
    votos = _votos_por_transacao(d)
    declarados = _participantes_por_transacao(d)
    falhas: list[str] = []
    verificados = 0
    for transacao in sorted(decisoes):
        if DECISAO_COMMIT not in decisoes[transacao].values():
            continue
        verificados += 1
        votos_tx = votos.get(transacao, {})
        participantes = declarados.get(transacao, set()) | set(votos_tx)
        sem_sim = sorted(p for p in participantes if votos_tx.get(p) != VOTO_SIM)
        if not participantes:
            falhas.append(
                f"transação {transacao}: COMMIT sem Participante conhecido, porque "
                "falta o `inicia_transacao` do Coordenador e não há Voto durável"
            )
        elif sem_sim:
            falhas.append(
                f"transação {transacao}: COMMIT sem o Voto SIM de {sem_sim} "
                f"(votos duráveis: {dict(sorted(votos_tx.items()))})"
            )
    return veredito(
        6, "Validade do 2PC", verificados, falhas,
        f"as {verificados} transação(ões) com COMMIT tiveram o Voto SIM de todos "
        "os Participantes",
    )


# Os invariantes, na ordem em que o relatório os apresenta.
INVARIANTES = (
    invariante_01_lider_unico,
    invariante_02_convergencia,
    invariante_03_maior_id_vivo,
    invariante_04_atomicidade,
    invariante_05_durabilidade,
    invariante_06_validade,
)


def verificar_todos(d: Dados) -> list[Veredito]:
    """Executa os invariantes na ordem e devolve os vereditos."""
    return [invariante(d) for invariante in INVARIANTES]


def relatar(
    vereditos: Sequence[Veredito],
    titulo: str = "Invariantes de correção",
    *,
    imprimir=print,
) -> bool:
    """Imprime o relatório e devolve True quando nenhum invariante reprovou.

    Um invariante sem caso a verificar (verificados == 0) é reportado à parte,
    como lacuna de dados, e NÃO reprova por si — é `scripts/verificar.py` que
    decide se a ausência de dados do algoritmo é aceitável. Avisos (como o
    Estado_Em_Duvida) aparecem no relatório mas não alteram o código de saída.
    """
    imprimir(f"\n=== {titulo} ===\n")
    largura = max((len(v.titulo) for v in vereditos), default=0)
    reprovados: list[Veredito] = []
    lacunas: list[Veredito] = []
    total_avisos = 0
    for v in vereditos:
        if v.verificados == 0:
            marca = "LACUNA"
            lacunas.append(v)
        elif v.ok:
            marca = "OK    "
        else:
            marca = "FALHA "
            reprovados.append(v)
        imprimir(f"  {marca} {v.numero:>2}. {v.titulo.ljust(largura)}  {v.detalhe}")
        for falha in v.falhas:
            imprimir(f"           {falha}")
        for aviso in v.avisos:
            imprimir(f"           aviso: {aviso}")
        total_avisos += len(v.avisos)

    imprimir("")
    if total_avisos:
        imprimir(f"{total_avisos} aviso(s), que não reprovam.")
    if lacunas:
        numeros = ", ".join(str(v.numero) for v in lacunas)
        imprimir(
            f"{len(lacunas)} invariante(s) sem dados a verificar (nº {numeros}): "
            "estes logs não têm o caso que eles conferem."
        )
    if reprovados:
        imprimir(
            f"{len(reprovados)} de {len(vereditos)} invariante(s) reprovado(s)."
        )
        return False
    imprimir(
        f"Nenhuma reprovação: {len(vereditos) - len(lacunas)} invariante(s) "
        "passaram, sem violação de safety."
    )
    return True
