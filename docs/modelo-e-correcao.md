# Modelo do sistema e correção dos algoritmos

Este documento fixa, por algoritmo, o modelo sob o qual o Trabalho 2 raciocina —
o modelo temporal, as propriedades dos canais, os tipos de falha, as hipóteses
de relógio, persistência e reinício — e as propriedades de *safety* e *liveness*
que cada algoritmo garante. Para cada propriedade, nomeia-se o invariante que a
testa sobre os logs (`invariante_01`..`invariante_05`, em
[`src/analise/invariantes.py`](../src/analise/invariantes.py)), de modo que a
afirmação de correção não fique só no texto: ela é verificável com

```bash
python scripts/verificar.py
```

Este é um documento de modelo, escrito à mão, e não uma tabela gerada a partir
de dados. A ligação propriedade↔invariante é concreta, com o nome de cada
invariante.

## Modelo comum aos dois algoritmos

**Canais.** A comunicação é HTTP/JSON ponto a ponto, servida por FastAPI e feita
por um cliente httpx (`src/comum/rede.py`). Os canais não são confiáveis: uma
Mensagem pode ser **perdida**, **atrasada** ou, sob partição, não **entregue**;
um retry pode **duplicar** uma Mensagem; e não há ordenação garantida entre
Mensagens distintas. Toda chamada carrega um **Deadline obrigatório** — não há
caminho de código que envie Mensagem sem timeout — e as falhas de transporte
viram resultados tipados (timeout, conexão recusada, indisponível), nunca
exceções silenciosas. É o Deadline que converte "não chegou" em uma decisão
explícita: o salto no Anel, o ABORT por timeout no 2PC.

**Relógios.** Nenhum algoritmo depende de relógios físicos sincronizados. Os
timestamps físicos no Log_Estruturado servem à medição de desempenho
(Tempo_de_Convergencia, Latencia_de_Commit), não à correção. A correção repousa
em ordem causal local e em Deadlines, não em tempo de parede comparável entre
Nós.

**Falhas (modelo comum).** As falhas entram por duas camadas, ambas dirigidas
por uma **Semente** registrada no log, o que torna cada Cenário repetível
(Requisitos 8.2, 8.4):

- **Aplicação** (`src/comum/falhas.py`, endpoints `/falha/*`): *crash* (o Nó
  deixa de responder), *pause* (congela N s), *delay* (atrasa respostas pelo
  stream `delay` da Semente) e *drop* (descarta Mensagem com probabilidade `p`
  pelo stream `drop`). *crash* e *pause* são ligados por comando e não consomem
  aleatoriedade; *drop* e *delay* são reproduzíveis dígito a dígito.
- **Rede** (Toxiproxy, `src/comum/toxiproxy.py` + `scripts/rede.py`): *latência*
  e *perda* como toxics, e **partição** modelada derrubando o proxy entre
  subconjuntos de Nós. A partição é o mecanismo de rede; o *crash* continua sendo
  de aplicação, porque modela a morte do processo, que o Toxiproxy não
  representa.

**Limitação do ambiente (Requisito 13.7).** Os contêineres rodam numa única
máquina. Eles são **Nós lógicos**, mas **não domínios de falha independentes**:
compartilham kernel, relógio, escalonador e memória físicos. Uma partição é
injetada logicamente (via Toxiproxy ou via `/falha/*`), não por uma falha física
real; um *crash* é a suspensão do processo, não a queda de um host. Por isso os
números absolutos (latências, tempos de convergência) valem para comparação
*relativa* entre configurações no mesmo ambiente, e cada linha de resultado
carimba o ambiente (`src/experimentos/ambiente.py`). As propriedades de
*correção* deste documento, porém, não dependem dessa limitação: elas são sobre a
ordem lógica dos eventos, que a emulação preserva.

---

## Eleição em Anel

### Modelo

- **Algoritmo.** Eleição em anel unidirecional com lista acumulada de ids. A
  Mensagem_Eleicao acumula os ids dos Nós que visita, e o Nó que a recebe com o
  próprio id na lista fecha a volta e elege o maior. Uma Mensagem_Lider anuncia o
  eleito e dá uma volta no Anel (`src/anel/maquina.py`).
- **Rodadas.** Cada eleição é uma rodada com id único, gerado por quem a inicia,
  que vai nas Mensagens e no log. Um Nó que já conhece o resultado de uma rodada
  ignora uma Mensagem_Eleicao atrasada dela, que fecharia a rodada de novo com
  outro Líder. A Mensagem_Lider para no primeiro Nó que já conhecia o resultado.
- **Temporal.** Assíncrono com timeouts e, na prática, parcialmente síncrono: o
  progresso depende de o Deadline detectar um Sucessor que não responde. Sem o
  Deadline, uma partição travaria a circulação. Com ele, o Nó tenta entregar ao
  Sucessor até três vezes dentro do Deadline, uma a cada terço dele, e só então
  salta para o próximo Nó vivo (`src/anel/no.py`, regra de salto em
  `src/anel/maquina.py`). Cada tentativa pode usar todo o prazo que resta, então
  uma resposta lenta ainda chega antes do salto.
- **Canais.** Unidirecionais na ordem do Anel: cada Nó só fala com seu Sucessor.
  Uma Mensagem pode se perder, e o reenvio dentro do Deadline a recupera sem
  tirar do Anel um Nó vivo. O Sucessor aceita a Mensagem respondendo com `ack`.
- **Falhas.** *Crash-stop* e *crash-recovery* (reinício do contêiner) de Nós, e
  partição Líder↔resto. Um Nó falho é contornado pela regra de salto. A detecção
  por timeout pode errar: um Nó lento ou pausado é saltado como se tivesse
  caído, e a eleição atrasada que ele repassa ao acordar é ignorada.
- **Relógio/persistência/reinício.** O Anel é **sem estado durável**: a eleição é
  idempotente e refeita quando disparada. Um Nó que reinicia volta sem memória de
  rodadas anteriores e participa da próxima eleição, porque cada Nó zera as
  suspeitas (`mortos`) ao entrar numa rodada nova e tira delas quem lhe manda
  Mensagem. Não há Log Durável no Anel, e a recuperação é reeleger.

### Propriedades e os invariantes que as testam

| Propriedade | Tipo | Invariante | Requisito |
| --- | --- | --- | --- |
| No máximo um Líder por Rodada_de_Eleicao concluída | *safety* | **`invariante_01_lider_unico`** | 4.7, 12.2 |
| Todos os Nós vivos convergem para o mesmo Líder | *liveness*/*safety* | **`invariante_02_convergencia`** | 4.5, 12.2 |
| O Líder eleito é o maior id vivo | *correctness* | **`invariante_03_maior_id_vivo`** | 4.7, 12.2 |

- **Unicidade do Líder** (`invariante_01_lider_unico`): agrupa os anúncios de
  Líder por id de rodada e exige um único valor de `lider` por rodada. Dois
  valores distintos reprovam. Como a eleição atrasada de uma rodada concluída é
  ignorada, uma rodada fecha uma vez só, e qualquer violação é defeito, não
  ruído.
- **Convergência dos vivos** (`invariante_02_convergencia`): por rodada, todos os
  observadores do anúncio devem concordar no `lider`, e o conjunto de
  observadores deve cobrir os Nós vivos da rodada. Divergência reprova; um vivo
  que nunca observou o anúncio também reprova, pois a rodada não convergiu para
  ele.
- **Maior id vivo** (`invariante_03_maior_id_vivo`): em cada rodada bem-formada, o
  Líder anunciado é `max(vivos)`. Respeita a morte por salto na própria rodada:
  com o maior id contornado (`sucessor_falho` no log, com o id da rodada), o alvo
  correto passa a ser o próximo maior id vivo, e elegê-lo é certo. O cenário
  `anel falha` mostra isso, elegendo 5 e, depois da queda do 5, reelegendo 4.

Tolerância: **zero** nos três. São igualdades entre inteiros (um Líder, um id),
sem variância amostral a absorver — a justificativa está em
[`docs/invariantes.md`](invariantes.md).

---

## Two-Phase Commit

### Modelo

- **Variante.** 2PC bloqueante, com crash-recovery do Coordenador e
  retransmissão da DECISAO até o ACK. Não há *presumed abort* nem o protocolo de
  terminação cooperativo, em que um Participante em dúvida consulta os outros.
  Por isso o bloqueio dura enquanto o Coordenador está fora.
- **Temporal.** Assíncrono com timeouts. O Coordenador envia PREPARE, coleta
  Votos até o Deadline de coleta e, se ele estoura antes de todos votarem, decide
  ABORT (Requisito 7.5). Os Deadlines de Voto e de Decisao são configuráveis.
- **Canais.** Estrela: Coordenador↔Participantes. PREPARE e DECISAO descem;
  VOTO e ACK sobem. Partições isolam o Coordenador de um ou mais Participantes.
  O Coordenador retransmite a DECISAO a cada Participante, a cada segundo, até
  receber o ACK dele, e cada tentativa tem o prazo do Transporte. O ACK é a
  Mensagem do protocolo que o Participante envia depois de gravar e aplicar a
  Decisao, e não a resposta HTTP. Um Participante que estava fora, ou que votou
  depois do ABORT por timeout, recebe a Decisao assim.
- **Transações.** Cada transação tem um id, e cada Nó guarda o estado dela num
  mapa por id. Várias transações correm nos mesmos Nós, seguidas ou ao mesmo
  tempo. Um `POST /transacao` com id já usado não abre rodada nova. Ele devolve a
  Decisao, que depois de um reinício a recuperação já trouxe do Log_Duravel, ou
  espera a rodada em curso, de modo que o cliente pode repetir o pedido sem risco.
- **Falhas.** *Crash-stop* e *crash-recovery* com estado reconstruído do
  Log_Duravel. O caso crítico é o **crash do Coordenador** depois de coletar
  alguns Votos e **antes** de difundir a Decisao.
- **Persistência (write-ahead).** O Coordenador grava a entrada em WAIT **antes**
  do PREPARE e a Decisao **antes** da DECISAO. O Participante grava o Voto antes
  de respondê-lo e a Decisao antes de aplicá-la. A ordem das `Acao` que a
  Máquina_de_Estados emite **é** a ordem write-ahead (`src/doispc/maquina.py`,
  `src/doispc/executor.py`). Os ACKs não vão ao log.
- **Reinício.** O Coordenador relê o Log_Duravel antes de servir. A transação
  decidida tem a Decisao difundida de novo a todos, já que os ACKs não são
  duráveis. A transação em WAIT vira ABORT, gravado e difundido, porque o
  Coordenador não guardou os Votos e não sabe se todos foram SIM. O Participante
  relê o próprio log e reporta a **mesma** Decisao de antes (evento
  `recuperacao`). Um que votou SIM e não tem Decisao durável volta em dúvida e
  sai dela quando a DECISAO chega.

### Propriedades e os invariantes que as testam

| Propriedade | Tipo | Invariante | Requisito |
| --- | --- | --- | --- |
| Atomicidade: nenhum COMMIT enquanto outro ABORT na mesma transação | *safety* | **`invariante_04_atomicidade`** | 6.7, 12.3 |
| Durabilidade: a Decisao não reverte após reinício | *safety* | **`invariante_05_durabilidade`** | 6.6, 6.8, 12.3 |
| *Liveness* bloqueante: Estado_Em_Duvida enquanto o Coordenador está fora | n/d (aviso) | **`invariante_05_durabilidade`** (ramo de aviso) | 8.5, 12.4 |

- **Atomicidade** (`invariante_04_atomicidade`): lê a Decisao durável de cada Nó
  (Coordenador e Participantes) e reprova se COMMIT e ABORT coexistem numa
  transação. Toma a **primeira** Decisao de cada Nó, pois, uma vez decidida, a
  transação não reverte (Requisito 6.8). Tolerância zero: atomicidade é
  tudo-ou-nada.
- **Durabilidade** (`invariante_05_durabilidade`, ramo de reprovação): quando um
  Nó registra uma `recuperacao`, a Decisao recuperada tem de bater com a Decisao
  durável daquele Nó. Divergência é reversão após reinício e reprova. O cenário
  `2pc crash-coord` reinicia o Coordenador, que registra a `recuperacao` da
  Decisao gravada antes do crash.
- **Bloqueio característico do 2PC** (`invariante_05_durabilidade`, ramo de
  aviso): um Participante que gravou Voto SIM mas não tem Decisao durável está em
  **Estado_Em_Duvida** (Requisito 8.5). Ele não pode decidir sozinho, porque não
  sabe se os outros votaram SIM. Nesta variante o bloqueio dura enquanto o
  Coordenador está fora. Quando ele volta, relê o log e difunde a Decisao, ou o
  ABORT de uma transação que estava em WAIT, e a retransmissão a entrega a quem
  perdeu a Mensagem. **Isso é comportamento correto do 2PC**, o preço da
  atomicidade sem um protocolo de terminação cooperativo, e não um defeito. O
  invariante o classifica como **aviso**: aparece no relatório, mas **não** muda
  o código de saída (Requisito 12.4). Tratá-lo como reprovação transformaria uma
  propriedade conhecida do 2PC num alarme falso. O aviso aparece nos logs
  coletados com o Coordenador ainda fora e some depois que ele se recupera.

### Por que o Deadline e o write-ahead não são detalhe de estilo

Duas regras do modelo sustentam diretamente as propriedades acima, e afrouxá-las
as quebra:

- **Deadline obrigatório.** Sem ele, uma partição Coordenador↔Participante
  travaria a coleta de Votos para sempre e o ABORT por timeout (Requisito 7.5)
  nunca ocorreria — a *liveness* na ausência de falhas deixaria de valer.
- **Ordem write-ahead.** Se a gravação da Decisao migrasse para depois da
  resposta de rede, um crash entre responder e gravar perderia a Decisao, e
  `invariante_05_durabilidade` passaria a reprovar numa reversão real.
- **Entrada em WAIT.** Sem ela, um Coordenador que cai antes de decidir volta sem
  saber que a transação existiu, e os Participantes que votaram SIM ficam em
  dúvida para sempre.

---

## Cenários que exercitam cada propriedade

Os cenários reproduzíveis (`scripts/cenario.py`, encadeados em
`scripts/demo.py`) deixam, sob `results/`, os logs que os invariantes consomem.
Cada um registra a **Semente** com que foi conduzido.

| Cenário | Fase exercitada | Invariante(s) relevante(s) | Resultado esperado |
| --- | --- | --- | --- |
| `anel normal` | eleição sem falha converge para o maior id (5) | 01, 02, 03 | passam |
| `anel falha` | elege 5, derruba o contêiner do 5 e reelege o maior vivo (4) | 01, 02, 03 | passam |
| `2pc commit` | todos SIM → COMMIT em todos | 04, 05 | passam |
| `2pc abort` | um NAO → ABORT atômico em todos | 04, 05 | passam |
| `2pc crash-coord` | o processo do Coordenador cai depois de gravar a Decisao, os Participantes ficam em dúvida, e o contêiner reiniciado se recupera do log | 04, 05 | passam, todos em COMMIT depois do reinício |

Em todos, `verificar.py` sai com código 0, porque as propriedades de *safety*
valem. No `2pc crash-coord`, o 05 confere a `recuperacao` do Coordenador
reiniciado contra a Decisao do log dele.
