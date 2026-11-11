# Proposta de roteiro do vídeo de demonstração (T2)

Vídeo de **~5 minutos**, com a participação dos **dois integrantes**. O enunciado
exige mostrar **pelo menos um cenário normal, uma falha injetada e a recuperação
ou consequência esperada**. Os cenários abaixo são os mesmos que o repositório já
executa de ponta a ponta (`scripts/cenario.py`), então o que aparece no vídeo é o
que o código realmente faz, sem encenação.

> Esta é uma proposta: ajustem a divisão de falas, o ritmo e o que mostrar na tela
> conforme preferirem. O orçamento de tempo soma ~5 min; cortem onde for preciso.

## Antes de gravar (checklist)

- `docker compose build` já rodado, imagem pronta (evita esperar no vídeo).
- Terminal com fonte grande e tema de alto contraste.
- Ter os documentos abertos para mostrar rápido: `docs/modelo-e-correcao.md` e
  `docs/resultados-2pc.md`.
- Opcional: rodar as baterias antes e deixar as figuras de `docs/figuras/` à mão
  para mostrar os gráficos sem esperar os ~10 min da bateria do 2PC.
- Ensaiar uma vez: o `2pc crash-coord` leva alguns segundos para reiniciar o
  contêiner e difundir a decisão.

## Divisão sugerida das falas

- **Integrante A** conduz a Eleição em Anel (normal + falha).
- **Integrante B** conduz o Two-Phase Commit (normal + crash do Coordenador).
- Abertura e fechamento podem ser divididos, uma frase cada.

---

## Bloco 1 — Abertura e visão geral (~40 s)

**Quem:** Integrante A.

O que dizer:
- Nomes, RAs e a disciplina.
- Os dois algoritmos escolhidos e por que são complementares: **Eleição de Líder
  em Anel** (tema de eleição) e **Two-Phase Commit** (tema de compromisso atômico).
- A ideia central da arquitetura em uma frase: cada Nó é um **contêiner
  independente** que conversa por **HTTP/JSON**, com **Deadline obrigatório** em
  toda chamada, e falhas injetadas de forma **repetível por Semente**.

O que mostrar:
- O `compose.yaml` (ou o diagrama da topologia, se fizerem um slide): 5 Nós do
  Anel, 1 Coordenador + 3 Participantes no 2PC, e o Toxiproxy.

---

## Bloco 2 — Eleição em Anel: cenário normal (~45 s)

**Quem:** Integrante A.

O que dizer:
- O anel é unidirecional ($1\to2\to3\to4\to5\to1$); a mensagem de eleição acumula
  os ids e o maior vence.
- A propriedade esperada: todos os vivos convergem para o **maior id vivo (5)**.

O que mostrar / rodar:
```bash
python scripts/cenario.py anel normal
```
- Apontar na saída: a eleição converge, o Líder é o **5**, e o cenário confere o
  desfecho contra o esperado (sai 0).

---

## Bloco 3 — Eleição em Anel: falha do Líder (~50 s)

**Quem:** Integrante A.

O que dizer:
- Agora matamos o contêiner do Líder (5). Com a detecção por timeout, o Nó que
  não consegue entregar ao sucessor **salta** para o próximo vivo e dispara uma
  reeleição.
- A consequência esperada: a reeleição converge para o **maior vivo restante (4)**.

O que mostrar / rodar:
```bash
python scripts/cenario.py anel falha
```
- Apontar: elege 5, derruba o 5, reelege **4**. Mencionar que os invariantes de
  unicidade, convergência e "maior id vivo" (01–03) passam.

---

## Bloco 4 — Two-Phase Commit: cenário normal (~45 s)

**Quem:** Integrante B.

O que dizer:
- 2PC com um Coordenador e três Participantes. Fase 1: PREPARE e coleta de Votos.
  Fase 2: DECISÃO (COMMIT se todos votam SIM, ABORT se algum vota NÃO).
- A ordem **write-ahead** é o ponto-chave: Voto e Decisão vão ao **Log Durável
  antes** de serem respondidos/aplicados.

O que mostrar / rodar (um dos dois, ou os dois se sobrar tempo):
```bash
python scripts/cenario.py 2pc commit    # todos SIM -> COMMIT atômico
python scripts/cenario.py 2pc abort     # um NÃO -> ABORT atômico
```
- Apontar: a decisão é **atômica** (ou todos COMMIT, ou todos ABORT).

---

## Bloco 5 — 2PC: crash do Coordenador e recuperação (~70 s) — clímax

**Quem:** Integrante B.

O que dizer:
- Este é o caso crítico. O Coordenador **grava a Decisão e cai antes de
  difundi-la**. Os três Participantes ficam **em dúvida** (votaram SIM, mas não
  sabem o desfecho) — e **bloqueiam**, porque este 2PC não tem terminação
  cooperativa.
- Destacar que o bloqueio é **comportamento correto do 2PC**, o preço da
  atomicidade, e não um defeito (o invariante o classifica como *aviso*).
- Ao reiniciar o contêiner, o Coordenador **relê o Log Durável** e **retransmite a
  DECISÃO até o ACK** de cada Participante. Todos decidem **COMMIT**.

O que mostrar / rodar:
```bash
python scripts/cenario.py 2pc crash-coord
```
- Apontar na saída: os Participantes em dúvida com o Coordenador fora → reinício →
  todos em COMMIT. A Decisão recuperada do log **bate** com a gravada antes do
  crash (durabilidade, invariante 05).
- Fechar com:
```bash
python scripts/verificar.py
```
  mostrando que **todos os invariantes de safety passam** (código de saída 0) e os
  avisos de dúvida desaparecem após a recuperação.

---

## Bloco 6 — Resultados e fechamento (~50 s)

**Quem:** Integrante B.

O que dizer / mostrar (usar as figuras de `docs/figuras/`):
- **Anel:** mensagens crescem como $2n-1$ (medido: 5,00 / 9,20 / 13,40 para
  $n=3,5,7$), batendo com a complexidade esperada.
- **2PC:** a latência de commit cresce em linha com os $2N$ atrasos de ida da rede
  (de ~8,5 ms a ~344,6 ms para 0→50 ms), e a recuperação fica **abaixo de 1 s**.
- Uma frase sobre **reprodutibilidade**: tudo é dirigido por Semente, cada
  resultado carimba o ambiente, e `analisar.py conferir` garante que as tabelas de
  `docs/` batem com os dados brutos versionados.
- Encerrar com a principal lição: a correção é verificada por **invariantes sobre
  os logs reais**, não só por inspeção do código.

---

## Orçamento de tempo (resumo)

| Bloco | Conteúdo | Tempo |
|:------|:---------|------:|
| 1 | Abertura e visão geral | ~40 s |
| 2 | Anel normal | ~45 s |
| 3 | Anel com falha do Líder | ~50 s |
| 4 | 2PC normal (commit/abort) | ~45 s |
| 5 | 2PC crash do Coordenador + recuperação | ~70 s |
| 6 | Resultados e fechamento | ~50 s |
| | **Total** | **~5 min** |

## Dicas de gravação

- Se a bateria do 2PC (~10 min) for longa demais para o vídeo, **não rode ao vivo**:
  mostre as figuras já geradas e cite os números.
- Os cenários (`cenario.py`) são rápidos e dão a demonstração "ao vivo" de
  correção — priorize-os no tempo de tela.
- Deixe o `verificar.py` para o final como "prova" de que as propriedades valem.
- Cuidado com o `2pc crash-coord`: espere o reinício completar antes de cortar.
