# Invariantes de correção

Seis propriedades que os logs dos Nós precisam satisfazer para que os dois algoritmos estejam
corretos. Os invariantes não conferem médias estatísticas de uma bateria. Eles verificam *safety* e
*liveness* sobre eventos discretos dos Logs_Estruturados e dos Logs_Duraveis que os Nós reais
deixam sob `results/`.

```bash
python scripts/verificar.py
```

O comando confere cada diretório `*_logs` de `results/` sozinho, porque cada um é uma
execução e os invariantes do Anel tomam como vivos os Nós que aparecem no log dela. Sai 0
quando nenhum invariante reprova em nenhum diretório, mesmo com avisos ou sem log algum, e 1
quando algum invariante reprova em algum deles. A implementação está em
[`src/analise/invariantes.py`](../src/analise/invariantes.py), com um invariante por função.

## Reprova, aviso e lacuna

O T2 distingue dois desfechos possíveis de um invariante além de "passou", e
este comando acrescenta um terceiro para dados ausentes. A distinção é decisão de
projeto, e não exigência do enunciado.

**Reprova.** Uma propriedade foi violada: dois Líderes na mesma rodada, um Nó vivo que não
conheceu o Líder da rodada, um Líder que não é o maior id vivo, COMMIT e ABORT coexistindo numa
transação, uma Decisao que reverteu após reinício, ou um COMMIT sem o Voto SIM de todos os
Participantes. Reprovar é a única coisa que muda o código de saída do comando para 1.

**Aviso.** Um fenômeno esperado do algoritmo, que *não* é defeito. O único caso é o Participante
que gravou Voto SIM e não tem Decisao durável, o Estado_Em_Duvida, qualquer que seja a causa. Ele
aparece em logs coletados enquanto o Coordenador está fora sem ter difundido a Decisao, e também
quando a DECISAO ainda não chegou ao Participante, por perda ou partição. Bloquear assim é
comportamento correto do 2PC, o preço da atomicidade sem um protocolo de terminação, e o aviso
aparece no relatório sem alterar o código de saída. Tratá-lo como reprovação transformaria uma
propriedade conhecida do 2PC num alarme falso.

**Lacuna.** Nenhum caso a verificar para aquele invariante. Ou só há logs do Anel, e os
invariantes do 2PC não têm transação alguma, ou o caso que o invariante confere não aconteceu:
sem reinício não há `recuperacao` com Decisao para o invariante 5, e sem COMMIT não há validade a
conferir no 6. A lacuna aparece à parte e, sozinha, não reprova. Quem roda só um cenário do Anel
não recebe reprovação pelos invariantes do 2PC que não exercitou. Cada cenário, porém, exige os
invariantes que demonstra (`MODOS` em `scripts/cenario.py`), e uma lacuna num deles reprova o
cenário. Assim um cenário sem log nenhum não passa.

## Como as tolerâncias são escolhidas

Nenhuma tolerância é um número redondo escolhido para caber nos dados. As propriedades são
predicados exatos sobre objetos discretos, como um Líder ou uma Decisao, e não médias temporais
de uma execução finita.

**A tolerância natural é zero.** Unicidade do Líder, convergência, "maior id
vivo", atomicidade e não-reversão são igualdades entre inteiros ou entre rótulos
(`COMMIT`/`ABORT`). Não há variância amostral a absorver: ou a igualdade vale, ou
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

## Os seis invariantes

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
Participante que volta em dúvida não traz Decisao e não conta. Sem uma
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

## Ligação com o modelo de correção

Cada invariante testa uma propriedade declarada em
[`docs/modelo-e-correcao.md`](modelo-e-correcao.md), que faz a ligação
propriedade↔invariante por nome. Os invariantes 1 a 3 cobrem a *safety* e a
*liveness* da Eleição em Anel, e os invariantes 4 a 6 cobrem a atomicidade, a
durabilidade e a validade do 2PC. A terminação do 2PC é conferida pelo cenário,
que conhece a falha injetada e sai 1 quando o desfecho difere do esperado. O
Estado_Em_Duvida é a *liveness* bloqueante do 2PC, classificada como aviso. O
cenário `2pc crash-coord` verifica os logs com o Coordenador fora, com o aviso, e
depois do reinício, sem ele. Os cenários reproduzíveis que exercitam cada propriedade estão em
`scripts/cenario.py`, e `scripts/demo.py` encadeia o `anel falha` e o `2pc crash-coord`.
