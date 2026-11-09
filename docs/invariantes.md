# Invariantes de correção

Seis propriedades que os logs dos Nós precisam satisfazer para que os dois
algoritmos estejam corretos. Em vez de conferir médias estatísticas de uma
bateria, eles verificam *safety* e
*liveness* sobre eventos discretos observados nos Logs_Estruturados e nos
Logs_Duraveis que os Nós reais deixam sob `results/`.

```bash
python scripts/verificar.py
```

O comando sai com código 0 quando nenhum invariante reprova — mesmo havendo
avisos — e 1 quando alguma propriedade de *safety* é violada (Requisito 12.1). A
implementação está em [`src/analise/invariantes.py`](../src/analise/invariantes.py),
com um invariante por função.

## Reprova, aviso e lacuna

O enunciado distingue dois desfechos possíveis de um invariante além de "passou"
(Requisito 12.4), e este comando acrescenta um terceiro para dados ausentes.

**Reprova.** Uma propriedade de *safety* foi violada: dois Líderes na mesma
rodada, um Nó vivo que não convergiu, um Líder que não é o maior id vivo, COMMIT
e ABORT coexistindo numa transação, uma Decisao que reverteu após reinício, ou
um COMMIT sem o Voto SIM de todos os Participantes.
Reprovar é a única coisa que muda o código de saída para 1.

**Aviso.** Um fenômeno esperado do algoritmo, que *não* é defeito. O único caso
é o Estado_Em_Duvida bloqueante do 2PC: quando o Coordenador cai depois de
coletar alguns Votos e antes de difundir a Decisao, um Participante que votou SIM
fica bloqueado, sem Decisao durável (Requisito 8.5). Isso é comportamento correto
do 2PC — o preço da atomicidade sem um protocolo de terminação — e aparece no
relatório como aviso, sem alterar o código de saída. Tratá-lo como reprovação
transformaria uma propriedade conhecida do 2PC num alarme falso permanente.

**Lacuna.** Nenhum caso a verificar para aquele invariante. Só há logs do Anel,
então os invariantes do 2PC não têm transação alguma. Ou o caso que o invariante
confere não aconteceu: sem reinício não há `recuperacao` para o 5, e sem COMMIT
não há validade a conferir no 6. Uma lacuna é reportada à parte e não reprova
por si. Quem roda só um cenário do Anel não deve receber reprovação pelos
invariantes do 2PC que não exercitou.

## Como as tolerâncias são escolhidas

Nenhuma tolerância é um número redondo escolhido para caber nos dados. As
propriedades são de *safety* exata
sobre objetos discretos — um Líder, uma Decisao — e não médias temporais de uma
execução finita.

**A tolerância natural é zero.** Unicidade do Líder, convergência, "maior id
vivo", atomicidade e não-reversão são igualdades entre inteiros ou entre rótulos
(`COMMIT`/`ABORT`). Não há variância amostral a absorver: ou a igualdade vale, ou
há um defeito. Fixar uma folga positiva aqui só mascararia erros, então a folga é
exatamente zero.

**A única folga é qualitativa, não numérica.** É a reclassificação do
Estado_Em_Duvida como aviso. Ela não relaxa um limiar — a condição `votou SIM e
não tem Decisao durável` é verificada com precisão —; apenas decide que esse
resultado verdadeiro é um fenômeno esperado, por determinação do enunciado
(Requisito 12.4), e não uma reprovação. A reprovação de durabilidade fica
reservada à reversão de uma Decisao já registrada.

**Cada rodada tem um id único.** Quem inicia a rodada gera o id, que vai na
Mensagem_Eleicao, na Mensagem_Lider e no evento `sucessor_falho`. Os invariantes
do Anel agrupam os anúncios de Líder por esse id, de modo que duas rodadas,
concorrentes ou do mesmo iniciador, não se confundam. Os Nós vivos de uma
rodada, quando o cenário não os informa explicitamente, são inferidos do log:
todos os Nós que emitiram algum evento do Anel, menos os saltados naquela rodada.
Um Nó saltado numa rodada pode estar vivo na seguinte. A inferência é
conservadora e casa com os cenários coletados, onde a morte de um Nó aparece
como o salto de quem o contornou.

## Os seis invariantes

### 1. Unicidade do Líder por rodada (Anel) — *safety*

Cada Rodada_de_Eleicao concluída anuncia um único Líder. Agrupamos os anúncios
observados nos eventos `recebeu` de Mensagem_Lider por id de rodada e exigimos
um único valor de `lider` por rodada. Dois valores distintos na mesma rodada
reprovam. Os Nós ignoram a eleição atrasada de uma rodada concluída, que a
fecharia de novo. Com dois Nós suspeitos por engano na mesma rodada, porém, a
volta ainda pode fechar duas vezes, como mostra
[`modelo-e-correcao.md`](modelo-e-correcao.md). Uma reprovação aponta um defeito
ou um erro de detecção desse tipo. Tolerância: zero. Liga-se à *safety* do
Requisito 4.7.

### 2. Convergência dos Nós vivos (Anel) — *liveness*/*safety*

Ao fim de uma rodada, todos os Nós vivos conhecem o mesmo Líder (Requisito 4.5).
Por rodada, todos os observadores do anúncio devem concordar no `lider`, e o
conjunto de observadores deve cobrir os Nós vivos daquela rodada. Divergência
entre observadores reprova; um Nó vivo que jamais observou o anúncio também
reprova, pois a rodada não convergiu para ele. Tolerância: zero — convergência é
um predicado exato, não uma média.

### 3. Líder é o maior id vivo (Anel) — *correctness*

Em cada rodada concluída, o Líder anunciado é igual ao maior id entre os Nós
vivos (Requisitos 4.7, glossário). Verificamos apenas rodadas bem-formadas (um
único Líder; a unicidade é do invariante 1) e comparamos o eleito com
`max(vivos)`. A tolerância é zero por ser igualdade de inteiros. Respeita a morte
por salto: com o maior id contornado como `sucessor_falho`, o alvo passa a ser o
próximo maior id vivo, e elegê-lo é correto.

### 4. Atomicidade do 2PC — *safety*

Nenhum Nó decide COMMIT enquanto outro decide ABORT para a mesma transação
(Requisito 12.3). Lemos a Decisao durável de cada Nó — Coordenador e
Participantes — a partir dos Logs_Duraveis, tomando a **primeira** Decisao de
cada Nó (uma vez decidida, a transação não reverte, Requisito 6.8), e reprovamos
se COMMIT e ABORT coexistem na mesma transação. Atomicidade é tudo-ou-nada, sem
gradação: tolerância zero.

Uma linha conta como entrada durável só quando tem exatamente as chaves do
Log_Duravel (`transacao`, `fase`, `valor`, `no`). O evento `grava_log` do
Log_Estruturado repete essas chaves junto com `ts` e `tipo`, e não conta. Assim
cada Decisao e cada Voto vêm do arquivo que o Nó relê ao reiniciar.

### 5. Durabilidade da Decisao do 2PC — *safety*

A Decisao sobrevive ao reinício (Requisitos 6.6, 6.8). Dois sinais no log
sustentam a propriedade:

- Quando um Nó registra uma `recuperacao` (releu o Log_Duravel após reinício), a
  `decisao` recuperada tem de bater com a Decisao durável daquele Nó para a
  transação. Divergência é reversão e reprova. Tolerância: zero.
- Um Participante que gravou Voto SIM mas não tem Decisao durável está em
  Estado_Em_Duvida, o bloqueio esperado do 2PC enquanto o Coordenador está fora.
  Isso vira **aviso**, nunca reprovação (Requisito 12.4).

A reprovação de durabilidade fica, portanto, restrita à reversão de uma Decisao
já registrada; o bloqueio característico do protocolo é documentado como aviso,
não como defeito. Só a `recuperacao` conta como caso verificado, e sem reinício
nos logs o invariante fica em lacuna, com os avisos de dúvida.

### 6. Validade do 2PC — *safety*

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
cenário `2pc crash-coord` mostra o bloqueio com o Coordenador fora e o fim dele
depois do reinício. Os cenários reproduzíveis que exercitam cada propriedade estão em
`scripts/cenario.py` e são encadeados por `scripts/demo.py`.
