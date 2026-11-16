# Modelo do sistema e correção dos algoritmos

Este documento fixa, por algoritmo e para a integração dos dois, o modelo sob o
qual o Trabalho 2 raciocina: o modelo temporal, as propriedades dos canais, os
tipos de falha e as hipóteses de relógio, persistência e reinício. Fixa também as
propriedades de *safety* e *liveness*, cada uma com o invariante que a testa sobre
os logs (`invariante_01` a `invariante_07`, em
[`src/analise/invariantes.py`](../src/analise/invariantes.py)) ou, na terminação
do 2PC e no failover da integração, com o cenário que a confere. A afirmação de
correção se confere com

```bash
python scripts/verificar.py
```

Este documento é escrito à mão e não é uma tabela gerada a partir de dados.

## Modelo comum aos dois algoritmos

**Canais (M2).** A comunicação é HTTP/JSON ponto a ponto, servida por FastAPI e feita
por um cliente httpx (`src/comum/rede.py`). Os canais não são confiáveis. Uma
Mensagem pode ser **perdida** ou **atrasada**, e sob partição não é
**entregue**. Um retry pode **duplicar** uma Mensagem, e não há ordenação
garantida entre Mensagens distintas. Toda chamada carrega um **Deadline
obrigatório**, porque o cliente não tem caminho que envie Mensagem sem timeout,
e as falhas de transporte viram resultados tipados (timeout, conexão recusada,
indisponível), nunca exceções silenciosas. É o Deadline que converte "não
chegou" em uma decisão explícita: o salto no Anel, o ABORT por timeout no 2PC.

**Relógios (M4).** Nenhum algoritmo depende de relógio físico, sincronizado ou
não. A correção repousa na ordem causal de cada Nó e nos Deadlines. O `ts` do
Log_Estruturado serve para ler o log, e nenhuma métrica o usa. Cada métrica é
medida assim:

- **Bateria do Anel** (`src/experimentos/runner_anel.py`): Tempo_de_Convergencia
  em passos lógicos de entrega e Mensagens contadas pelo runner em processo,
  ambos reproduzíveis dígito a dígito pela Semente.
- **Bateria do 2PC** (`src/experimentos/condutor_doispc.py`): mede os Nós reais
  no compose, com o Toxiproxy entre o Coordenador e os Participantes, e o
  condutor mede com `time.monotonic`. A latência de commit vai do
  `POST /transacao` à resposta, que o Coordenador dá quando a Decisao já está no
  Log_Duravel. As mensagens de uma transação são os eventos `envia` dela nos logs
  de todos os Nós. O tempo de recuperação vai do `start` do Coordenador derrubado
  até todos os Participantes reportarem a Decisao. Esses tempos são de relógio e
  variam entre execuções, e a Semente só identifica a Réplica.
- **Cenários** (`scripts/cenario.py`): o condutor mede com `time.monotonic`, no
  próprio processo. A latência de commit vai do `POST /transacao` à resposta do
  Coordenador, que sai quando ele decide. O tempo de convergência do Anel vai do
  pedido de eleição até todos os vivos reportarem o Líder em `/estado`, e o de
  recuperação do 2PC vai do `start` do Coordenador até todos os Participantes
  reportarem a Decisao. Na integração, `tempo_failover_s` vai do erro do
  `POST /transacao` de `c3-tx-a` à resposta de `c2-tx-b` e inclui a reeleição, e
  `tempo_recuperacao_tx_a_s` vai do `start` do candidato 3 até os três
  Participantes reportarem a Decisao de `c3-tx-a`, a mesma definição da
  recuperação do 2PC. O condutor consulta `/estado` a cada 10 ms, que é a
  resolução de cada tempo que termina numa resposta de `/estado`, nos cenários e
  na bateria.

**Falhas (modelo comum).** As falhas entram por duas camadas, e cada Cenário
registra no log a **Semente** que o conduziu.

- **Aplicação** (`src/comum/falhas.py`, endpoints `/falha/*`): *crash* (o Nó
  responde 503 a toda Mensagem e não envia nenhuma), *pause* (o próximo evento
  do Nó, recebimento ou envio, dorme N s), *delay* (atrasa cada Mensagem pelo
  stream `delay` da Semente) e *drop* (descarta cada Mensagem com probabilidade
  `p`, pelo stream `drop`). *crash* e *pause* são ligados por comando e não
  consomem aleatoriedade. *drop* e *delay* se repetem dígito a dígito com a
  mesma Semente, desde que o Nó receba e envie as Mensagens na mesma ordem.
- **Rede** (Toxiproxy, `src/comum/toxiproxy.py` e `scripts/rede.py`): *latência*
  como toxic e **partição de entrada**, pela desativação do proxy de um Nó.
  Todo Nó alcança o destino pelo proxy do destino, então desativá-lo corta as
  Mensagens que chegam ao Nó, e não as que ele envia. Isso basta para isolar um
  Nó do Anel ou um Participante, porque eles só enviam em resposta a uma
  Mensagem recebida. O Nó do Anel repassa o que recebeu, e o Participante vota
  depois do PREPARE e confirma depois da DECISAO. O que o Coordenador envia por
  conta própria passa pelo proxy do Participante e é cortado junto.

A perda de Mensagens entra pela aplicação (*drop*) e pela bateria do Anel, e não
pelo Toxiproxy. O *crash* fica na aplicação, porque modela a morte do processo,
que o Toxiproxy não representa.

**Limitação do ambiente.** Os contêineres rodam numa única
máquina. Eles são **Nós lógicos**, mas **não domínios de falha independentes**:
compartilham kernel, relógio, escalonador e memória físicos. Uma partição é a
desativação de um proxy do Toxiproxy, e não uma falha física de rede. Um *crash*
é a morte do processo (`docker compose kill`, ou a saída do Coordenador logo
depois de gravar a Decisao) ou o modo crash do controlador, e não a queda de um
host. Por isso os números absolutos (latências, tempos de convergência) valem
para comparação *relativa* entre configurações no mesmo ambiente, e cada linha
de resultado carimba o ambiente (`src/experimentos/ambiente.py`). As
propriedades de *correção* deste documento, porém, não dependem dessa
limitação: elas são sobre a ordem lógica dos eventos, que a emulação preserva.

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
- **Temporal (M1).** Parcialmente síncrono. Os atrasos de mensagem e de
  processamento não têm limite garantido, mas quase sempre cabem no Deadline, e
  é com essa hipótese que o Deadline detecta um Sucessor que não responde. Sem o
  Deadline, uma partição travaria a circulação. Com ele, o Nó tenta entregar ao
  Sucessor até três vezes dentro do Deadline, uma a cada terço dele, e só então
  salta para o próximo Nó vivo (`entregar` em `src/comum/rede.py`, regra de salto em
  `src/anel/maquina.py`). Cada tentativa pode usar todo o prazo que resta, então
  uma resposta lenta ainda chega antes do salto.
- **Canais (M2).** Unidirecionais na ordem do Anel: cada Nó só fala com seu Sucessor.
  Uma Mensagem pode se perder, e o reenvio dentro do Deadline a recupera sem
  tirar do Anel um Nó vivo. O Sucessor aceita a Mensagem respondendo com `ack`.
- **Falhas (M3).** *Crash-stop* e *crash-recovery* (reinício do contêiner) de
  Nós, e partição de entrada do Líder, que o isola do resto. Um Nó falho é
  contornado pela regra de salto. A detecção é por timeout e pode errar: um Nó
  lento ou pausado é saltado como se tivesse caído. A suspeita volta a ser certa
  quando os atrasos cabem de novo no Deadline, a semântica *fail-noisy*. A
  eleição atrasada que um Nó saltado repassa ao acordar é ignorada por quem já
  fechou a rodada.
- **Relógio, persistência e reinício (M4).** O Anel é **sem estado durável**. Um
  Nó que reinicia volta sem memória de rodadas anteriores e participa da próxima
  eleição (cenário `anel reinicio`), porque cada Nó zera as suspeitas (`mortos`) ao entrar numa rodada nova
  e tira delas quem lhe manda Mensagem. Não há Log Durável no Anel. Sobre a
  rede, a eleição só começa por `POST /iniciar-eleicao`, chamado pelo cenário,
  pelo teste ou pelo operador, e na bateria em processo o runner abre a rodada.
  Nenhum Nó inicia uma rodada sozinho, nem ao suspeitar do Líder, e a
  recuperação é disparar outra eleição.
- **Nós (M7).** Cinco Nós no compose e 3, 5 e 7 na bateria, nunca menos que os
  três que o enunciado pede (A5). O cenário de falha derruba o Líder e ainda
  deixa quatro vivos, o bastante para a reeleição dar a volta no Anel sem ele.

### Propriedades (M5) e como são verificadas (M6)

| Propriedade | Tipo | Verificação |
| --- | --- | --- |
| No máximo um Líder por Rodada_de_Eleicao concluída | *safety* | **`invariante_01_lider_unico`** |
| Todos os Nós vivos convergem para o mesmo Líder | *liveness*/*safety* | **`invariante_02_convergencia`** |
| O Líder eleito é o maior id vivo | *correctness* | **`invariante_03_maior_id_vivo`** |

- **Unicidade do Líder** (`invariante_01_lider_unico`): agrupa os anúncios de
  Líder por id de rodada e exige um único valor de `lider` por rodada. Dois
  valores distintos reprovam. O descarte da eleição atrasada evita o segundo
  Líder quando um Nó lento é saltado e responde depois. Na eleição do 1 com o 5
  pausado, o 1 já recebeu do 4 a lista sem o 5, fechou a rodada e ignora a lista
  que o 5 repassa ao acordar (`test_pause_no_5_nao_gera_dois_lideres`). Com a
  detecção que pode errar, porém, o algoritmo não garante a unicidade em toda
  execução. Na eleição do 1, se o 4 suspeita por engano do 5, que está lento, e
  do 1, que recusa as três tentativas de entrega, ele salta os dois e o 2 fecha
  a volta com o Líder 4. Quando o 5 responde, repassa ao 1 a lista com o próprio
  id, e o 1, que nunca recebeu a lista do 4, fecha a mesma rodada com o Líder 5
  (`test_dois_nos_suspeitos_por_engano_fecham_a_rodada_duas_vezes`). Por isso a
  unicidade é verificada em cada execução, e uma reprovação aponta um defeito ou
  um erro de detecção como esse.
- **Convergência dos vivos** (`invariante_02_convergencia`): por rodada, todos os
  observadores do anúncio devem concordar no `lider`, e o conjunto de
  observadores deve cobrir os Nós vivos da rodada. Divergência reprova. Um vivo
  que nunca observou o anúncio também reprova, pois a rodada não convergiu para
  ele.
- **Maior id vivo** (`invariante_03_maior_id_vivo`): em cada rodada bem-formada, o
  Líder anunciado é `max(vivos)`. Respeita a morte por salto na própria rodada:
  com o maior id contornado (`sucessor_falho` no log, com o id da rodada), o alvo
  correto passa a ser o próximo maior id vivo, e elegê-lo é certo. O cenário
  `anel falha` mostra isso, elegendo 5 e, depois da queda do 5, reelegendo 4.

Tolerância: **zero** nos três. São igualdades entre inteiros (um Líder, um id),
sem variância amostral a absorver. A justificativa está em
[`docs/invariantes.md`](invariantes.md).

---

## Two-Phase Commit

### Modelo

- **Variante.** 2PC bloqueante, com crash-recovery do Coordenador e
  retransmissão da DECISAO até o ACK. Não há *presumed abort* nem o protocolo de
  terminação cooperativo, em que um Participante em dúvida consulta os outros.
  Por isso o bloqueio dura enquanto o Coordenador está fora.
- **Nós (M7).** Um Coordenador e três Participantes. Com mais de um Participante
  a atomicidade tem o que conferir, e com três o cenário de abort tem quem vote SIM
  e quem vote NAO, e o crash do Coordenador deixa vários Participantes em dúvida.
- **Temporal (M1).** Parcialmente síncrono, como o Anel. O Coordenador envia
  PREPARE, coleta Votos até o Deadline de coleta e, se ele estoura antes de
  todos votarem, decide ABORT. Os Deadlines de Voto e de Decisao
  são configuráveis. A atomicidade e a validade não dependem de tempo, e um
  timeout que erra só produz um ABORT desnecessário. A terminação depende de os
  atrasos voltarem a caber no Deadline.
- **Canais (M2).** Estrela: Coordenador↔Participantes. PREPARE e DECISAO descem,
  VOTO e ACK sobem. Uma partição corta as Mensagens do Coordenador a um
  Participante, e a retransmissão da DECISAO o alcança depois da cura (cenário
  `2pc particao`).
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
- **Falhas (M3).** *Crash-stop* e *crash-recovery* do Coordenador e dos
  Participantes, com o estado reconstruído do Log_Duravel, e partição entre o
  Coordenador e um Participante. O crash do
  Coordenador tem dois casos. Depois de gravar a Decisao e antes de difundi-la,
  os Participantes que votaram SIM ficam em dúvida até ele voltar e difundir a
  Decisao gravada (cenário `2pc crash-coord`). Em WAIT, antes de decidir, ele
  volta sem os Votos e decide ABORT
  (`test_coordenador_reiniciado_em_wait_aborta`). A detecção é por timeout e
  pode errar, a mesma semântica *fail-noisy* do Anel: um Participante lento é
  tratado como falho, e o Coordenador aborta.
- **Persistência (M4, write-ahead).** O Coordenador grava a entrada em WAIT **antes**
  do PREPARE e a Decisao **antes** da DECISAO. O Participante grava o Voto antes
  de respondê-lo e a Decisao antes de aplicá-la. A ordem das `Acao` que a
  Máquina_de_Estados emite **é** a ordem write-ahead (`src/doispc/maquina.py`,
  `src/doispc/executor.py`). Os ACKs não vão ao log. A durabilidade vale contra
  **crash de processo**: cada entrada recebe `fsync` antes do efeito que depende
  dela, mas o diretório do log não recebe `fsync` quando o arquivo é criado.
  Perder uma entrada já gravada exigiria a queda do sistema operacional, que o
  ambiente de contêineres numa só máquina não produz (A6).
- **Reinício (M4).** O Coordenador relê o Log_Duravel antes de servir. A transação
  decidida tem a Decisao difundida de novo a todos, já que os ACKs não são
  duráveis. A transação em WAIT vira ABORT, gravado e difundido, porque o
  Coordenador não guardou os Votos e não sabe se todos foram SIM. O Participante
  relê o próprio log e reporta a **mesma** Decisao de antes (evento
  `recuperacao`). Um que votou SIM e não tem Decisao durável volta em dúvida e
  sai dela quando a DECISAO chega. O cenário `2pc crash-coord-part` mostra os
  dois casos no contêiner real. Aplicar a Decisao é só um evento de log nesta
  implementação, sem efeito durável próprio, e por isso a recuperação trata toda
  Decisao durável como aplicada.

### Propriedades (M5) e como são verificadas (M6)

| Propriedade | Tipo | Verificação |
| --- | --- | --- |
| Atomicidade: nenhum COMMIT enquanto outro ABORT na mesma transação | *safety* | **`invariante_04_atomicidade`** |
| Durabilidade: a Decisao não reverte após reinício | *safety* | **`invariante_05_durabilidade`** |
| Validade: COMMIT só com o Voto SIM de todos os Participantes | *safety* | **`invariante_06_validade`** |
| Coordenador único: cada transação tem um só Coordenador | *safety* | **`invariante_07_coordenador_unico`** |
| Terminação sem falha: todos decidem, COMMIT com todos SIM e ABORT com um NAO | *liveness* | `cenario.py 2pc commit` e `2pc abort`, prazo de 10 s |
| Terminação com o Coordenador de volta: todo Participante decide | *liveness* | `cenario.py 2pc crash-coord`, prazo de 30 s |
| Terminação com o Coordenador de volta depois do crash de um Participante em dúvida: todo Participante decide | *liveness* | `cenario.py 2pc crash-coord-part`, prazo de 30 s |
| *Liveness* bloqueante: Estado_Em_Duvida enquanto o Coordenador está fora | n/d (aviso) | **`invariante_05_durabilidade`** (ramo de aviso) |

- **Atomicidade** (`invariante_04_atomicidade`): lê a Decisao durável de cada Nó
  (Coordenador e Participantes) e reprova se COMMIT e ABORT coexistem numa
  transação. Toma a **primeira** Decisao de cada Nó, pois, uma vez decidida, a
  transação não reverte. Tolerância zero: atomicidade é
  tudo-ou-nada.
- **Durabilidade** (`invariante_05_durabilidade`, ramo de reprovação): quando um
  Nó registra uma `recuperacao` com Decisao, ela tem de bater com a Decisao
  durável daquele Nó. Divergência é reversão após reinício e reprova. O cenário
  `2pc crash-coord` reinicia o Coordenador, que registra a `recuperacao` da
  Decisao gravada antes do crash. O `2pc crash-coord-part` reinicia também o
  Participante 11, em dúvida e depois de decidir. A segunda `recuperacao` traz a
  Decisao e é o caso de Participante do 05. A primeira não tem Decisao a comparar,
  e o cenário confere pelo `/estado` que o 11 voltou com Voto SIM e em dúvida.
  Sem `recuperacao` com Decisao nos logs não houve reinício a conferir, e o 05
  fica em lacuna.
- **Validade** (`invariante_06_validade`): numa transação com COMMIT, todo
  Participante tem Voto SIM no próprio Log_Duravel. Os Participantes são os que o
  Coordenador lista no evento `inicia_transacao` e os que gravaram Voto, então um
  COMMIT que não esperou um deles também reprova. ABORT é sempre válido. Sem a
  validade, um sistema que sempre aborta, ou que confirma sem ouvir todos,
  passaria na atomicidade.
- **Coordenador único** (`invariante_07_coordenador_unico`): os ids que gravaram
  INICIO, emitiram `inicia_transacao` ou foram a `origem` de um PREPARE ou DECISAO
  recebido são os Coordenadores da transação, e mais de um reprova.
- **Terminação** (`scripts/cenario.py`): o cenário registra o desfecho esperado
  ao lado do observado e sai 1 quando eles diferem. No `2pc commit` e no
  `2pc abort`, cada Participante tem de decidir até 10 s depois da resposta do
  Coordenador. No `2pc crash-coord`, os três ficam em dúvida com o Coordenador
  fora e têm de decidir COMMIT até 30 s depois do reinício. No
  `2pc crash-coord-part` o prazo é o mesmo, com o 11 reiniciado em dúvida antes
  do Coordenador. A conferência fica no cenário porque só ele sabe que falha
  injetou e o que esperar dela.
- **Bloqueio característico do 2PC** (`invariante_05_durabilidade`, ramo de
  aviso): um Participante que gravou Voto SIM mas não tem Decisao durável está em
  **Estado_Em_Duvida**. Ele não pode decidir sozinho, porque não
  sabe se os outros votaram SIM. Nesta variante o bloqueio dura enquanto o
  Coordenador está fora. Quando ele volta, relê o log e difunde a Decisao, ou o
  ABORT de uma transação que estava em WAIT, e a retransmissão a entrega a quem
  perdeu a Mensagem. **Isso é comportamento correto do 2PC**, o preço da
  atomicidade sem um protocolo de terminação cooperativo, e não um defeito. O
  invariante o classifica como **aviso**: aparece no relatório, mas **não** muda
  o código de saída. Tratá-lo como reprovação transformaria uma
  propriedade conhecida do 2PC num alarme falso. O cenário `2pc crash-coord`
  confere os logs duas vezes. Com o Coordenador fora, a verificação dá o aviso
  dos três Participantes, e depois do reinício o aviso some.

### Por que o Deadline e o write-ahead não são detalhe de estilo

Duas regras do modelo sustentam diretamente as propriedades acima, e afrouxá-las
as quebra:

- **Deadline obrigatório.** Sem ele, uma partição entre o Coordenador e um
  Participante travaria a coleta de Votos para sempre, o ABORT por timeout nunca
  ocorreria e a transação não terminaria. O cenário `2pc particao` mostra o
  ABORT no prazo.
- **Ordem write-ahead.** Se a gravação da Decisao migrasse para depois da
  resposta de rede, um crash entre responder e gravar perderia a Decisao, e
  `invariante_05_durabilidade` passaria a reprovar numa reversão real.
- **Entrada em WAIT.** Sem ela, um Coordenador que cai antes de decidir volta sem
  saber que a transação existiu, e os Participantes que votaram SIM ficam em
  dúvida para sempre.

---

## Integração: o Líder do Anel coordena o 2PC

### Modelo

- **Problema.** Um serviço de transações atômicas sobre três Participantes, com três
  candidatos a Coordenador. O Líder do Anel formado pelos candidatos aceita as transações, e
  quando ele cai outro candidato assume as novas. Cada candidato roda no mesmo processo um Nó
  do Anel e um Coordenador do 2PC (`src/integracao/candidato.py`), e a pilha fica em
  `compose.integracao.yaml`.
- **Nós (M7).** Três candidatos, no anel 1 → 2 → 3 → 1, e três Participantes. Com três
  candidatos, a queda do Líder ainda deixa um anel de dois para reeleger, e o candidato que
  volta tem o maior id e retoma a liderança na eleição seguinte.
- **Temporal (M1).** Parcialmente síncrono, herdado dos dois algoritmos. Não há detector de
  falha do Líder. Quem percebe a queda é o cliente, pelo erro ou pelo prazo do
  `POST /transacao`, e é ele que dispara a eleição com `POST /iniciar-eleicao`.
- **Canais (M2).** Cada candidato recebe as Mensagens dos dois algoritmos num só
  `POST /mensagem` e despacha pelo tipo: `eleicao` e `lider` vão ao Nó do Anel, `voto` e `ack`
  ao Coordenador, e outro tipo recebe 400. Entre os candidatos corre o Anel. Entre cada
  candidato e os Participantes corre a estrela do 2PC, e o Participante responde à `origem` da
  Mensagem quando ela está em `DOISPC_COORDENADORES`. Uma origem fora dessa lista é recusada
  antes da Máquina_de_Estados, com o evento `origem_desconhecida` (`src/doispc/participante.py`).
- **Falhas (M3).** *Crash-recovery* do candidato, que derruba juntos o Nó do Anel e o
  Coordenador, como a queda de um processo real. O Deadline do Anel pode suspeitar de um
  candidato vivo, e então dois candidatos se julgam Líderes ao mesmo tempo. A *safety* das
  transações se mantém, porque cada uma leva no id o prefixo do candidato que a criou
  (`c3-tx-a`, de `id_da_transacao`), e só ele a coordena.
- **Persistência e reinício (M4).** Cada candidato tem o próprio Log_Duravel num volume, e o
  Anel continua sem estado durável. O candidato que volta recupera só as próprias transações
  e não conhece Líder até a eleição seguinte. Nenhum candidato assume a transação de outro.
  Só o log do 3 sabe se ele gravou o COMMIT de `c3-tx-a`, e abortá-la no lugar dele violaria
  a atomicidade. Por isso a transação em dúvida espera o 3 voltar, o bloqueio do 2PC.
- **Limite.** Não há dados nem travas. Num banco, `c2-tx-b` esperaria as travas que
  `c3-tx-a` segura nos itens em comum. Aqui ela comita com `c3-tx-a` em dúvida porque nada
  liga as duas.

### Propriedades (M5) e como são verificadas (M6)

| Propriedade | Tipo | Verificação |
| --- | --- | --- |
| Coordenador único: cada transação tem um só Coordenador, mesmo com dois candidatos que se julgam Líderes | *safety* | **`invariante_07_coordenador_unico`** |
| Atomicidade, durabilidade e validade de cada transação | *safety* | **`invariante_04_atomicidade`**, **`invariante_05_durabilidade`** e **`invariante_06_validade`** |
| Unicidade, convergência e maior id vivo entre os candidatos | *safety*, *liveness* e *correctness* | **`invariante_01_lider_unico`**, **`invariante_02_convergencia`** e **`invariante_03_maior_id_vivo`** |
| Só o Líder aceita transação | roteamento, não *safety* | `cenario.py integracao normal`, campo `recusa_nao_lider` |
| Transações novas voltam a comitar depois da reeleição | *liveness* | `cenario.py integracao failover`, campos `lideres_pos_falha` e `decisoes_participantes_tx_b`, prazo de 10 s |
| A transação em dúvida termina quando o Coordenador dela volta | *liveness* bloqueante | `cenario.py integracao failover`, campos `em_duvida_tx_a_durante_tx_b` e `decisoes_participantes_tx_a`, prazo de 30 s |

- **Coordenador único** (`invariante_07_coordenador_unico`): no
  `test_dois_lideres_comitam_com_prefixos_distintos_e_nenhum_invariante_reprova`, o 3 não
  responde durante a segunda eleição, que elege o 2, e volta com o estado de antes, crendo
  que ainda é o Líder. Os dois comitam cinco transações com os mesmos nomes, que só o prefixo
  separa, e 04, 06 e 07 conferem as dez sem reprovação.
- **Só o Líder aceita transação**: o candidato que não é o Líder responde 409 com o id do
  Líder, sem chamar o Coordenador, e grava `transacao_recusada`. Isso não é *safety*. Um
  candidato que se julga Líder por engano aceita transações com o próprio prefixo, e o 07
  continua valendo.
- **Failover** (`integracao failover`): o 3 grava o COMMIT de `c3-tx-a` e cai antes de
  difundi-lo (`DOISPC_CAIR_APOS_DECIDIR`), com os três Participantes em dúvida
  (`em_duvida_tx_a`). O cliente elege o 2, que comita `c2-tx-b` sem conhecer `c3-tx-a`
  (`tx_a_no_novo_lider` nulo). O 3 volta, relê o Log_Duravel e difunde o COMMIT, e a eleição
  seguinte o reintegra como Líder (`lideres_finais`). A flag de queda continua ligada no 3
  reiniciado, mas a recuperação de uma transação decidida só difunde a Decisao, sem gravar, e
  o cenário não manda transação nova a ele. O 05 confere a `recuperacao` de `c3-tx-a` no
  candidato 3 contra a Decisao do log dele.

---

## Cenários que exercitam cada propriedade

Cada cenário reproduzível (`scripts/cenario.py`) grava os logs dos Nós em
`results/cenario_<algoritmo>_<modo>_logs/`, que `verificar.py` confere sozinho,
e registra a **Semente** com que foi conduzido. `scripts/demo.py` encadeia o
`anel falha` e o `2pc crash-coord`. Os invariantes que a última coluna dá como
aprovados são os que o modo exige em `MODOS`.

| Cenário | Fase exercitada | Invariante(s) relevante(s) | Resultado esperado |
| --- | --- | --- | --- |
| `anel normal` | eleição sem falha converge para o maior id (5) | 01, 02, 03 | passam |
| `anel falha` | elege 5, derruba o contêiner do 5 e reelege o maior vivo (4) | 01, 02, 03 | passam |
| `anel reinicio` | elege 5, derruba o 5, reelege 4, reinicia o contêiner do 5, que volta sem memória, e a terceira eleição o reintegra como Líder | 01, 02, 03 | passam nas três rodadas |
| `2pc commit` | todos SIM → COMMIT em todos | 04, 05, 06, 07 | 04, 06 e 07 passam, 05 em lacuna |
| `2pc abort` | um NAO → ABORT atômico em todos | 04, 05, 06, 07 | 04 e 07 passam, 05 e 06 em lacuna |
| `2pc crash-coord` | o processo do Coordenador cai depois de gravar a Decisao, os Participantes ficam em dúvida, e o contêiner reiniciado se recupera do log | 04, 05, 06, 07 | passam, com o aviso de dúvida dos três na verificação feita com o Coordenador fora e todos em COMMIT depois do reinício |
| `2pc crash-coord-part` | o Coordenador cai depois de gravar a Decisao, o Participante 11 cai e volta em dúvida, o Coordenador volta e todos decidem COMMIT, e o 11 cai e volta com a Decisao | 04, 05, 06, 07 | passam, o 05 com a recuperação do Coordenador e a segunda do 11, e com o aviso de dúvida dos três na verificação feita com o Coordenador fora |
| `2pc particao` | corta a rota até o Participante 11, o Coordenador aborta no prazo de Votos, e depois da cura a retransmissão leva o ABORT ao 11 | 04, 05, 06, 07 | 04 e 07 passam, 05 e 06 em lacuna, ABORT em todos e transação concluída |
| `integracao normal` | elege o candidato 3, o 1 recusa a transação com 409 e aponta o 3, que comita `c3-tx-n` em todos | 01 a 07 | 01 a 04, 06 e 07 passam, 05 em lacuna |
| `integracao failover` | o candidato 3 cai depois de gravar o COMMIT de `c3-tx-a`, o cliente elege o 2, que comita `c2-tx-b` com `c3-tx-a` em dúvida, e o 3 volta, difunde o COMMIT e a eleição seguinte o reintegra como Líder | 01 a 07 | passam nas três rodadas e nas duas transações, o 05 com a recuperação do candidato 3 |

Em todos, `verificar.py` sai com código 0 e o desfecho observado é o esperado.
`cenario.py` sai 1 se um invariante reprova, se um invariante exigido termina sem
caso verificado ou se o desfecho difere do esperado. No `2pc crash-coord`, o 05
confere a `recuperacao` do Coordenador reiniciado contra a Decisao do log dele, e
no `2pc crash-coord-part` também a segunda `recuperacao` do 11. No
`integracao failover`, ele confere a `recuperacao` de `c3-tx-a` no candidato 3.

## Falhas do modelo e onde são demonstradas

Cada falha que o modelo declara tem uma demonstração que a exercita. Os cenários e os testes
de compose rodam sobre os contêineres, e os demais testes rodam sem Docker, com os Nós em
processo ou servidos localmente.

| Algoritmo | Falha | Demonstração | Verificação |
|:--|:--|:--|:--|
| Anel | crash-stop do Líder | cenário `anel falha`, `test_crash_do_lider_reelege_o_maior_vivo_e_o_no_volta` | invariantes 01 a 03 nos logs |
| Anel | reinício do Nó sem memória | cenário `anel reinicio` | 01 a 03 nas três rodadas e o `/estado` do 5 vazio ao voltar |
| Anel | partição de entrada do Líder | `test_particao_do_lider_reelege_o_maior_vivo` e `scripts/demo_rede_toxiproxy.py`, pelo Toxiproxy | 01 a 03 nos logs do teste |
| Anel | suspeita errada de um Nó lento ou pausado | `test_pause_no_5_nao_gera_dois_lideres`, e a demo de rede com latência acima do Deadline | 01 a 03 no teste, e na demo o 2 suspeita do 3, que responde em `/saude` |
| Anel | perda de Mensagem | bateria com p = 0,05, `test_reenvia_dentro_do_deadline_antes_de_saltar` | mensagens contra (2n − 1)/(1 − p) em [`resultados-anel.md`](resultados-anel.md) |
| 2PC | crash do Coordenador depois de decidir | cenário `2pc crash-coord`, bateria de recuperação, `test_coordenador_reiniciado_depois_de_decidir_entrega_a_decisao` | 04 a 07, com o aviso de dúvida na verificação feita com o Coordenador fora |
| 2PC | crash do Coordenador em WAIT | `test_coordenador_reiniciado_em_wait_aborta`, `test_coordenador_reiniciado_antes_de_decidir_aborta` | ABORT gravado e difundido depois do reinício |
| 2PC | Participante fora | `test_timeout_de_votos_leva_a_abort`, `test_id_repetido_devolve_a_decisao_gravada` | ABORT no prazo de Votos |
| 2PC | crash do Participante em dúvida com o Coordenador fora | cenário `2pc crash-coord-part`, `test_participante_reiniciado_em_duvida_decide_quando_o_coordenador_volta` | o 11 volta em dúvida pelo próprio log, todos decidem COMMIT, e o 05 confere a recuperação do Coordenador e a segunda do 11 |
| 2PC | reinício do Participante com o log | `test_participante_recupera_decisao_apos_reinicio`, `test_recuperacao_apos_reinicio_rele_do_arquivo`, em memória e em arquivo | a mesma Decisao depois de reler o log |
| 2PC | partição entre o Coordenador e um Participante | cenário `2pc particao` | 04, 07 e ABORT em todos depois da cura |
| 2PC | Participante lento, que o timeout trata como falho | `test_voto_depois_do_abort_sai_da_duvida_pela_retransmissao` | o Voto atrasado não muda o ABORT, e a retransmissão tira o Participante da dúvida |
| 2PC | DECISAO perdida | `test_decisao_perdida_chega_pela_retransmissao` | a retransmissão entrega a Decisao |
| Integração | crash do Líder que coordena, depois de decidir | cenário `integracao failover` | 01 a 07, `c2-tx-b` em COMMIT com `c3-tx-a` em dúvida, e `c3-tx-a` em COMMIT depois do reinício do 3 |
| Integração | candidato que não responde numa eleição e volta crendo ser o Líder, com dois Líderes | `test_dois_lideres_comitam_com_prefixos_distintos_e_nenhum_invariante_reprova` | 01 a 03 nas duas rodadas, e 04, 06 e 07 nas dez transações dos dois Líderes |
