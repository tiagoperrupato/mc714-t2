# Invariantes de correção

Nove propriedades que os logs dos Nós precisam satisfazer para que os três algoritmos estejam
corretos. Os invariantes não conferem médias estatísticas de uma bateria. Eles verificam *safety* e
*liveness* sobre eventos discretos dos Logs_Estruturados e dos Logs_Duraveis que os Nós reais
deixam sob `results/`.

```bash
python scripts/verificar.py
```

O comando confere cada diretório `*_logs` de `results/` sozinho, porque cada um é uma
execução, e os invariantes do Anel e o 9 do Bully tomam como vivos os Nós que aparecem no log
dela. Sai 0 quando nenhum invariante reprova em nenhum diretório, mesmo com avisos ou sem log
algum, e 1 quando algum invariante reprova em algum deles. A implementação está em
[`src/analise/invariantes.py`](../src/analise/invariantes.py), com um invariante por função.

## Reprova, aviso e lacuna

O T2 distingue dois desfechos possíveis de um invariante além de "passou", e
este comando acrescenta um terceiro para dados ausentes. A distinção é decisão de
projeto, e não exigência do enunciado.

**Reprova.** Uma propriedade foi violada: dois Líderes na mesma rodada, um Nó vivo que não
conheceu o Líder da rodada, um Líder que não é o maior id vivo, COMMIT e ABORT coexistindo numa
transação, uma Decisao que reverteu após reinício, um COMMIT sem o Voto SIM de todos os
Participantes, uma transação com dois Coordenadores, um Nó do Bully que aceita Líder de id menor
que o seu, ou um Nó vivo do Bully que termina sem o maior id vivo como Líder. Reprovar é a única
coisa que muda o código de saída do comando para 1.

**Aviso.** Um fenômeno esperado do algoritmo, que *não* é defeito. O único caso é o Participante
que gravou Voto SIM e não tem Decisao durável, o Estado_Em_Duvida, qualquer que seja a causa. Ele
aparece em logs coletados enquanto o Coordenador está fora sem ter difundido a Decisao, e também
quando a DECISAO ainda não chegou ao Participante, por perda ou partição. Bloquear assim é
comportamento correto do 2PC, o preço da atomicidade sem um protocolo de terminação, e o aviso
aparece no relatório sem alterar o código de saída. Tratá-lo como reprovação transformaria uma
propriedade conhecida do 2PC num alarme falso.

**Lacuna.** Nenhum caso a verificar para aquele invariante. Ou os logs são de outro algoritmo,
ou o caso que o invariante confere não aconteceu. Num diretório do Anel, os invariantes do 2PC e
do Bully não têm caso. Num do Bully, os do Anel e do 2PC também não, porque todo evento do Bully
leva o prefixo `bully_`. Sem reinício não há `recuperacao` com Decisao para o invariante 5, e sem
COMMIT não há validade a conferir no 6. A lacuna aparece à parte e, sozinha, não reprova. Quem
roda só um cenário do Anel não recebe reprovação pelos invariantes do 2PC que não exercitou. Cada
cenário, porém, exige os invariantes que demonstra (`MODOS` em `scripts/cenario.py`), e uma
lacuna num deles reprova o cenário. Assim um cenário sem log nenhum não passa.

## Como as tolerâncias são escolhidas

Nenhuma tolerância é um número redondo escolhido para caber nos dados. As propriedades são
predicados exatos sobre objetos discretos, como um Líder ou uma Decisao, e não médias temporais
de uma execução finita.

**A tolerância natural é zero.** Unicidade do Líder, convergência, "maior id
vivo", atomicidade, não-reversão, Coordenador único, Líder não menor e "vivos no
maior id vivo" são igualdades ou comparações entre inteiros, ou igualdades entre
rótulos (`COMMIT`/`ABORT`). Não há variância amostral a absorver: ou a igualdade vale, ou
há um defeito. Fixar uma folga positiva aqui só mascararia erros, então a folga é
exatamente zero.

**A única folga é qualitativa, não numérica.** É a reclassificação do
Estado_Em_Duvida como aviso. Ela não relaxa um limiar, porque a condição `votou
SIM e não tem Decisao durável` é verificada com precisão. Ela só decide, por
projeto, que esse resultado verdadeiro é o bloqueio esperado do 2PC, e não uma
reprovação. A reprovação de durabilidade fica
reservada à reversão de uma Decisao já registrada.

**Cada rodada tem um id único.** Quem inicia a rodada gera o id, que vai na
Mensagem_Eleicao, na Mensagem_Lider e no evento `sucessor_falho`. Os invariantes
do Anel agrupam os anúncios de Líder por esse id, de modo que duas rodadas,
concorrentes ou do mesmo iniciador, não se confundam. Os Nós vivos de uma
rodada são inferidos do log: todos os Nós que emitiram algum evento do Anel,
menos os saltados naquela rodada. Um Nó saltado numa rodada pode estar vivo na
seguinte. Um Nó vivo saltado por engano conta como morto naquela rodada, e por
isso o Líder certo dela é o maior id entre os não saltados.

## Os nove invariantes

### 1. Unicidade do Líder por rodada (Anel, *safety*)

Cada Rodada_de_Eleicao concluída anuncia um único Líder. Agrupamos os anúncios
observados nos eventos `recebeu` de Mensagem_Lider por id de rodada e exigimos
um único valor de `lider` por rodada. Dois valores distintos na mesma rodada
reprovam. Os Nós ignoram a eleição atrasada de uma rodada concluída, que a
fecharia de novo. Com dois Nós suspeitos por engano na mesma rodada, porém, a
volta ainda pode fechar duas vezes, como mostra
[`modelo-e-correcao.md`](modelo-e-correcao.md). Uma reprovação aponta um defeito
ou um erro de detecção desse tipo. Tolerância: zero. Liga-se à *safety* da
eleição.

### 2. Convergência dos Nós vivos (Anel, *liveness* e *safety*)

Ao fim de uma rodada, todos os Nós vivos conhecem o mesmo Líder.
Por rodada, todos os observadores do anúncio devem concordar no `lider`, e o
conjunto de observadores deve cobrir os Nós vivos daquela rodada. Divergência
entre observadores reprova. Um Nó vivo que jamais observou o anúncio também
reprova, pois a rodada não convergiu para ele. Tolerância: zero, porque
convergência é um predicado exato, não uma média.

### 3. Líder é o maior id vivo (Anel, *correctness*)

Em cada rodada concluída, o Líder anunciado é igual ao maior id entre os Nós
vivos. Verificamos apenas rodadas com um único Líder, porque a unicidade é do
invariante 1, e comparamos o eleito com `max(vivos)`. A tolerância é zero por
ser igualdade de inteiros. Respeita a morte por salto: com o maior id contornado
como `sucessor_falho`, o alvo passa a ser o próximo maior id vivo, e elegê-lo é
correto.

### 4. Atomicidade do 2PC (*safety*)

Nenhum Nó decide COMMIT enquanto outro decide ABORT para a mesma transação.
Lemos nos Logs_Duraveis a Decisao durável de cada Nó, Coordenador e
Participantes, e tomamos a **primeira** Decisao de cada um, porque uma vez
decidida a transação não reverte. Reprovamos se COMMIT e ABORT coexistem na
mesma transação. Atomicidade é tudo-ou-nada, sem gradação: tolerância zero.

Uma linha conta como entrada durável só quando tem exatamente as chaves do
Log_Duravel (`transacao`, `fase`, `valor`, `no`). O evento `grava_log` do
Log_Estruturado repete essas chaves junto com `ts` e `tipo`, e não conta. Assim
cada Decisao e cada Voto vêm do arquivo que o Nó relê ao reiniciar.

### 5. Durabilidade da Decisao do 2PC (*safety*)

A Decisao sobrevive ao reinício. Dois sinais no log sustentam a propriedade:

- Quando um Nó registra uma `recuperacao` com Decisao, porque releu o Log_Duravel
  ao reiniciar, essa Decisao tem de ser a primeira Decisao gravada no Log_Duravel
  daquele Nó para a transação. Divergência é reversão e reprova. Tolerância: zero.
- Um Participante que gravou Voto SIM e não tem Decisao durável está em
  Estado_Em_Duvida, e isso vira **aviso**, nunca reprovação.

A reprovação de durabilidade fica restrita à reversão de uma Decisao já
registrada. Só a `recuperacao` com Decisao conta como caso verificado. A de um
Participante que volta em dúvida não traz Decisao a comparar e não conta. O
aviso de dúvida só a cobre quando o log termina em dúvida, como na verificação
com o Coordenador fora. No `2pc crash-coord-part` o Participante 11 volta em
dúvida, decide depois, e a segunda `recuperacao` dele é que conta. Sem uma
`recuperacao` com Decisao nos logs, o invariante fica em lacuna, com os avisos
de dúvida.

### 6. Validade do 2PC (*safety*)

COMMIT só quando todos os Participantes votaram SIM. Para cada transação com
alguma Decisao COMMIT, todo Participante precisa de um Voto SIM no próprio
Log_Duravel. Os Participantes são os que o Coordenador lista no evento
`inicia_transacao` do Log_Estruturado e os que gravaram Voto, de modo que um Voto
NAO e um Participante que não votou reprovam igualmente. ABORT é sempre válido e
não conta como caso. A atomicidade sozinha aprovaria um sistema que sempre
aborta, ou que confirma sem ouvir todos. Tolerância: zero.

### 7. Coordenador único por transação do 2PC (*safety*)

Cada transação tem um só Coordenador. Os ids que agiram como Coordenador de uma
transação vêm de três sinais: a entrada INICIO do Log_Duravel, que só o
Coordenador grava, o evento `inicia_transacao` e a `origem` de todo PREPARE ou
DECISAO que um Participante recebe. O `recebe` de Voto e ACK fica de fora, porque
ali a origem é um Participante. Mais de um id na mesma transação reprova, já que
dois Coordenadores difundem Decisoes independentes e a atomicidade passaria a
depender de qual delas chega primeiro. Tolerância: zero.

Na integração, é o 07 que sustenta a atomicidade com dois Líderes. O Deadline do
Anel pode suspeitar de um candidato vivo, e então dois candidatos se julgam Líderes
e aceitam transações ao mesmo tempo. Cada transação leva no id o prefixo de quem a
criou, e por isso nenhuma tem dois Coordenadores. O
`test_dois_lideres_comitam_com_prefixos_distintos_e_nenhum_invariante_reprova`
produz os dois Líderes em processo e confere o 07 nas dez transações deles. O
cenário `integracao failover` o confere em `c3-tx-a`, coordenada pelo 3, e em
`c2-tx-b`, que o 2 coordena enquanto o 3 está fora.

### 8. Líder não menor que o Nó (Bully, *safety*)

Nenhum Nó do Bully aceita Líder de id menor que o seu. O Nó grava um evento `bully_lider` sempre
que o Líder dele muda, e cada um é um aceite. O id aceito tem de ser maior ou igual ao de quem
aceita, e o Nó que assume aceita a si mesmo, o que conta como igual. Os dois Líderes
transitórios passam. No retorno do maior, ele e o Líder que ele substitui só aceitaram a si
mesmos. O menor que assume por suspeita errada só é aceito pelos Nós abaixo dele, e o maior vivo
rejeita o anúncio dele. Um aceite de id menor reprova. Tolerância: zero.

### 9. Vivos no maior id vivo (Bully, *liveness*)

Ao fim do cenário, todo Nó vivo tem como Líder o maior id vivo. Os vivos são os Nós com algum
evento `bully_*` menos os que o condutor derrubou e não religou. O condutor grava cada `kill` e
`start` em `condutor.jsonl`, no diretório de logs do cenário, na ordem em que agiu. É a fonte de
quem estava vivo, independente dos logs dos Nós, que não registram a própria morte. O último
`bully_lider` de cada vivo tem de ser o maior id vivo, e um vivo sem aceite também reprova.
Tolerância: zero.

Como vale o último aceite, o 9 exige um diretório por cenário, porque juntar os três cenários do
Bully juntaria os últimos aceites. O prazo da *liveness* é a espera do cenário, que coleta os
logs depois de ver os vivos ociosos com o Líder esperado, ou depois de 30 s. A propriedade vale
sob o modelo síncrono do Bully. Um COORDENADOR de um menor que chega a um Nó depois do anúncio
do maior, por um atraso acima dos prazos, faz o 9 reprovar, como o 1 diante de um erro de
detecção no Anel.

## Ligação com o modelo de correção

Cada invariante testa uma propriedade declarada em
[`docs/modelo-e-correcao.md`](modelo-e-correcao.md), que faz a ligação
propriedade↔invariante por nome. Os invariantes 1 a 3 cobrem a *safety* e a
*liveness* da Eleição em Anel, os invariantes 4 a 7 cobrem a atomicidade, a
durabilidade, a validade e o Coordenador único do 2PC, e os invariantes 8 e 9
cobrem a *safety* e a *liveness* do Bully. A terminação do 2PC é conferida pelo
cenário, que conhece a falha injetada e sai 1 quando o desfecho difere do
esperado. O Estado_Em_Duvida é a *liveness* bloqueante do 2PC, classificada como
aviso. O cenário `2pc crash-coord` verifica os logs com o Coordenador fora, com o
aviso, e depois do reinício, sem ele. O `2pc crash-coord-part` faz o mesmo com o
Participante 11 reiniciado em dúvida antes da verificação com o Coordenador fora.
Na integração, os invariantes 1 a 3 conferem as rodadas dos candidatos, e os 4 a 7
as transações que eles coordenam. Os três cenários do Bully exigem o 8 e o 9. Os
cenários reproduzíveis que exercitam cada propriedade estão em `scripts/cenario.py`,
e `scripts/demo.py` encadeia o `anel falha` e o `2pc crash-coord`.
