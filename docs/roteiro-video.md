# Roteiro do vídeo de demonstração

Vídeo de cerca de cinco minutos com os dois integrantes, Tiago e Pietro (D3 do enunciado). Ele
mostra um cenário normal, falhas injetadas e a recuperação ou a consequência esperada de cada uma.
Cada bloco roda ao vivo um cenário de `scripts/cenario.py`, que sobe a pilha, conduz o algoritmo,
coleta os logs dos Nós e confere os invariantes, e o que aparece na tela é o que o código faz. O
caso normal está dentro dos cenários de falha: a primeira eleição do `anel falha`, a eleição da
subida do `bully retorno` e a transação `c2-tx-b` do `integracao failover`.

> **O `results/` fica intacto.** Todo comando de cenário deste roteiro grava num diretório
> temporário, com `--results "$(mktemp -d)"`. Sem essa opção, o cenário reescreve os JSON e os
> logs versionados em `results/`. Se isso acontecer, `git restore results/` volta aos dados
> publicados.

## Antes de gravar

Na raiz do projeto, a pasta do `compose.yaml`, com o venv ativo (`source .venv/bin/activate`):

```bash
docker compose build                                                  # imagem única dos Nós
docker compose down -v --remove-orphans                               # anel e 2PC
docker compose -f compose.integracao.yaml down -v --remove-orphans    # integração
docker compose -f compose.bully.yaml down -v --remove-orphans         # Bully
docker ps                                                             # nenhum contêiner do projeto no ar
```

- **Entre os blocos**, repita os três `down`. Cada cenário derruba a própria pilha ao fim, mas um
  cenário interrompido deixa contêineres e portas presos.
- **Dois terminais lado a lado** nos blocos 2 a 5. O da esquerda roda o cenário, e o da direita o
  laço de `curl` em `/estado` do bloco, que imprime `porta ... fora` enquanto a pilha não está no ar.
- **Abertos para mostrar:** a Fig. 1 de `docs/relatorio/relatorio.pdf` e as figuras de
  `docs/figuras/`.
- **Fonte grande** e tema de alto contraste no terminal. A subida e a derrubada de cada pilha levam
  alguns segundos e podem ser cortadas na edição.
- **Ensaiar uma vez.** Os tempos impressos pelos cenários mudam a cada execução, então a fala só
  cita os que aparecem na tela.

## Tempos

| Bloco | Quem | Conteúdo | Tempo |
|:--|:--|:--|--:|
| 1 | Pietro e Tiago | Abertura e arquitetura | 40 s |
| 2 | Pietro | Anel: eleição e falha do Líder (`anel falha`) | 40 s |
| 3 | Pietro | 2PC: crash do Coordenador e recuperação (`2pc crash-coord`) | 45 s |
| 4 | Tiago | Integração: failover do Líder que coordena (`integracao failover`) | 50 s |
| 5 | Tiago | Bully: retorno do maior id (`bully retorno`) | 40 s |
| 6 | Pietro e Tiago | Resultados | 60 s |
| 7 | Tiago | Fechamento | 25 s |
| | | **Total** | **300 s, 5 min** |

O bloco 3b, da falha combinada, é opcional e dura 25 s. Com ele, o bloco 6 perde a última fala, a
da recuperação do Coordenador, e cai para 45 s. O bloco 7 perde as duas primeiras frases, sobre os
dados brutos, o `verificar.py` e o `conferir`, que ficam só na tela, e cai para 15 s. O total continua em 5 min.

---

## Bloco 1. Abertura e arquitetura (40 s)

**Na tela:** a Fig. 1 do relatório, com as camadas de um Nó, o Toxiproxy, o condutor e o caminho dos
logs até `verificar.py` e `analisar.py`. Se sobrar tempo, os serviços de cada pilha:

```bash
docker compose config --services
docker compose -f compose.integracao.yaml config --services
docker compose -f compose.bully.yaml config --services
```

**Fala:**
- **Pietro:** "Somos Pietro Fernandes Magaldi e Tiago Perrupato Antunes, e este é o Trabalho 2 de
  MC714. Implementamos a eleição em anel, o two-phase commit e, como terceiro algoritmo, o Bully."
- **Tiago:** "Cada Nó é um contêiner que só fala HTTP com os outros, e toda chamada tem Deadline. A
  lógica de cada algoritmo é uma máquina de estados pura, sem rede. As falhas entram pela aplicação
  ou pelo Toxiproxy, com uma Semente registrada."
- **Pietro:** "Cada cenário termina conferindo invariantes sobre os logs dos Nós. No fim, o Líder do
  anel coordena o 2PC, e mostramos o que acontece quando ele cai."

## Bloco 2. Anel: eleição e falha do Líder (40 s)

Terminal da direita, com os cinco Nós do anel:

```bash
while sleep 0.5; do
  for p in 8001 8002 8003 8004 8005; do
    curl -s --max-time 0.5 "localhost:$p/estado" || printf 'porta %s fora' "$p"; echo
  done
  echo ---
done
```

Terminal da esquerda:

```bash
python scripts/cenario.py anel falha --results "$(mktemp -d)"
```

**Na tela:**
- A linha `[cenario] anel/falha (semente=0): {...}`, que é o JSON do registro. Apontar
  `lideres_iniciais`, com os cinco no 5, `lideres_pos_falha`, com os quatro vivos no 4,
  `tempo_reeleicao_s` e `"desfecho": {"ok": true, ...}`.
- À direita, `"lider":5` nos cinco, depois `porta 8005 fora`, `"mortos":[5]` no 4 e `"lider":4`
  nos quatro vivos.
- O veredito dos invariantes: `OK` no 1, no 2 e no 3, e `Nenhuma reprovação`.

**Fala (Pietro):**
- "O anel é 1, 2, 3, 4, 5 e volta ao 1. A primeira eleição é o caso normal: a lista de ids dá a
  volta, e o maior, o 5, vence."
- "Agora o cenário mata o contêiner do 5 e pede outra eleição ao Nó 1. Nenhum Nó inicia eleição
  sozinho. O 4 tenta entregar ao 5 três vezes dentro do Deadline de 1 s e salta para o 1."
- "Os quatro vivos ficam com o 4, e os invariantes de Líder único, convergência e maior id vivo
  passam."

## Bloco 3. 2PC: crash do Coordenador e recuperação (45 s)

Terminal da direita, com os três Participantes:

```bash
while sleep 0.5; do
  for p in 8011 8012 8013; do
    curl -s --max-time 0.5 "localhost:$p/estado" || printf 'porta %s fora' "$p"; echo
  done
  echo ---
done
```

Terminal da esquerda:

```bash
python scripts/cenario.py 2pc crash-coord --results "$(mktemp -d)"
```

**Na tela:**
- `[cenario] Coordenador caiu (...). Em dúvida: [11, 12, 13]`, e à direita os três com
  `"voto":"SIM"`, `"decisao":null` e `"em_duvida":true`.
- `[cenario] verificação com o Coordenador fora:`, com as três linhas `aviso: ... Estado_Em_Duvida`
  e `Nenhuma reprovação`.
- `[cenario] Coordenador reiniciado. Em ... s: {11: 'COMMIT', 12: 'COMMIT', 13: 'COMMIT'}`, e à
  direita `"decisao":"COMMIT"`.
- No JSON do registro, `avisos_com_coordenador_fora` igual a 3 e `tempo_recuperacao_s`. No veredito
  final, `OK` do 4 ao 7, com o 5 em `as 1 recuperação(ões) após reinício reproduzem a Decisao
  registrada`.

**Fala (Pietro):**
- "Um Coordenador e três Participantes, todos votando SIM. Cada Voto e cada Decisão vão ao Log
  Durável antes de serem respondidos ou aplicados."
- "O Coordenador grava o COMMIT e morre antes de avisar. Os três votaram SIM e não sabem o
  resultado: estão em dúvida e bloqueiam, porque este 2PC não tem terminação cooperativa."
- "A verificação com o Coordenador fora mostra isso como aviso, e não como reprovação. É o preço
  da atomicidade."
- "O contêiner volta, relê o log e retransmite a DECISAO até o ACK de cada um. Todos ficam em
  COMMIT, e o invariante 05 confere que a Decisão relida é a gravada."

## Bloco 3b. Falha combinada, opcional (25 s)

```bash
python scripts/cenario.py 2pc crash-coord-part --results "$(mktemp -d)"
```

**Na tela:**
- `[cenario] Participante 11 reiniciado com o Coordenador fora: {'voto': 'SIM', 'decisao': None,
  'em_duvida': True}`.
- `[cenario] Participante 11 reiniciado depois da Decisao: {'voto': 'SIM', 'decisao': 'COMMIT',
  'em_duvida': False}`.
- No veredito, o 5 com `as 2 recuperação(ões) após reinício reproduzem a Decisao registrada`.

**Fala (Tiago):** "Com o Coordenador fora, o Participante 11 também cai. Ele volta pelo próprio log,
com Voto SIM e ainda em dúvida, porque não tem a quem perguntar. Quando o Coordenador volta, os
três decidem COMMIT. O 11 cai de novo e volta já com o COMMIT, e o 05 confere duas recuperações,
a do Coordenador e a segunda do 11."

## Bloco 4. Integração: failover do Líder que coordena (50 s)

Terminal da direita, com os Participantes da integração na transação `c3-tx-a`:

```bash
while sleep 0.5; do
  for p in 8031 8032 8033; do
    curl -s --max-time 0.5 "localhost:$p/estado?transacao=c3-tx-a" || printf 'porta %s fora' "$p"; echo
  done
  echo ---
done
```

Terminal da esquerda:

```bash
python scripts/cenario.py integracao failover --results "$(mktemp -d)"
```

**Na tela, as linhas `[cenario]` na ordem:**
- `eleição no candidato 1: Líderes {1: 3, 2: 3, 3: 3}`.
- `candidato 3 caiu depois de decidir c3-tx-a` e `Participantes em dúvida em c3-tx-a: [11, 12, 13]`,
  com `"em_duvida":true` à direita.
- `o cliente dispara a eleição no candidato 1: Líderes {1: 2, 2: 2}`.
- `candidato 2: c2-tx-b COMMIT em ...`.
- `c3-tx-a segue em dúvida em [11, 12, 13], e o /estado dela no candidato 2 traz transacao=None`.
- `candidato 3 reiniciado`, com `"decisao":"COMMIT"` à direita, e `eleição com o 3 de volta:
  Líderes {1: 3, 2: 3, 3: 3}`.
- no veredito, `OK` do 1 ao 7, com o 7 em `as 2 transação(ões) tiveram um único Coordenador`.

**Fala (Tiago):**
- "Agora os dois algoritmos juntos. Três candidatos formam um anel, e cada um roda um Nó do anel e
  um Coordenador. Só o Líder aceita transação, e o id dela leva o prefixo de quem a criou."
- "O Líder 3 grava o COMMIT de c3-tx-a e cai. Não há detector de falha: o cliente vê o erro e pede
  uma eleição, que elege o 2."
- "O 2 comita c2-tx-b, mas não assume c3-tx-a. Só o log do 3 sabe que ela foi COMMIT, então ela
  espera o 3 voltar, o bloqueio do 2PC."
- "O 3 volta, difunde o COMMIT, e a eleição seguinte o devolve à liderança. Cada transação teve um
  só Coordenador, o que o invariante 07 confere."

## Bloco 5. Bully: retorno do maior id (40 s)

Terminal da direita, com os cinco Nós do Bully:

```bash
while sleep 0.5; do
  for p in 8041 8042 8043 8044 8045; do
    curl -s --max-time 0.5 "localhost:$p/estado" || printf 'porta %s fora' "$p"; echo
  done
  echo ---
done
```

Terminal da esquerda:

```bash
python scripts/cenario.py bully retorno --results "$(mktemp -d)"
```

**Na tela:**
- `[cenario] eleições da subida: Líderes {1: 5, 2: 5, 3: 5, 4: 5, 5: 5}`.
- `[cenario] 5 derrubado e eleição pedida ao 1: Líderes {1: 4, 2: 4, 3: 4, 4: 4} em ... s`, com
  `"lider":4` à direita e `porta 8045 fora`.
- `[cenario] 5 religado, sem eleição pedida: Líderes {1: 5, ..., 5: 5} em ... s`, com `"lider":5` e
  `"fase":"ociosa"` nos cinco.
- No JSON, `eventos_condutor` com o `kill` e o `start` do 5. No veredito, `OK` no 8 e no 9.

**Fala (Tiago):**
- "No Bully os cinco Nós se falam todos com todos. Quem convoca manda ELEICAO aos maiores, e quem
  não recebe OK no prazo assume."
- "Derrubamos o 5 e pedimos eleição ao 1. O 4 espera o OK do 5 pelo prazo de 2,5 s e assume."
- "Religamos o 5 sem pedir eleição. Ele convoca ao subir, é o maior id e assume na hora. No anel,
  o 5 que volta só lidera quando alguém pede outra eleição."
- "Nenhum Nó aceitou Líder menor que ele, e todo vivo terminou no maior id vivo: os invariantes 08
  e 09."

## Bloco 6. Resultados (60 s)

**Na tela:** cada figura ao lado das tabelas versionadas que ela resume, todas geradas por
`scripts/analisar.py` e conferidas pelo `conferir`.
- `docs/figuras/anel_bully.png`, com as seções "Mensagens × complexidade esperada" de
  `docs/resultados-anel.md` e de `docs/resultados-bully.md` e "Anel × Bully nas mesmas condições"
  de `docs/resultados-bully.md`.
- `docs/figuras/doispc_latencia.png`, com "Latência de commit × latência de rede" e "Mensagens e
  latência × complexidade esperada" de `docs/resultados-2pc.md`.
- `docs/figuras/doispc_recuperacao.png`, com "Tempo de recuperação × latência de rede" de
  `docs/resultados-2pc.md`.

**Fala**, com os números daquelas tabelas:
- **Pietro:** "Com perda de 5% por tentativa, o anel gasta 13,40 Mensagens com sete Nós, perto das
  13,68 previstas por 2n menos 1 dividido por 1 menos p."
- **Tiago:** "O Bully gasta mais. Com sete Nós, são 51,00 Mensagens até não restar nenhuma em
  trânsito, perto das 50,53 do pior caso com perda. Em troca, ele converge em 2 saltos sem perda e
  em 2,60 na média com perda, contra 13,40 do anel."
- **Pietro:** "No 2PC cada transação custa 12 Mensagens, 4N com três Participantes. A latência de
  commit vai de 8,5 ms sem atraso a 344,6 ms com 50 ms, acompanhando os seis atrasos de ida do
  caminho crítico, porque os PREPARE saem em sequência."
- **Tiago:** "A recuperação do Coordenador fica abaixo de 1 s na média em todos os níveis, mas o pior
  caso chega a 1,552 s. Quase todo o tempo é a partida do contêiner e do interpretador."

## Bloco 7. Fechamento (25 s)

Os dois comandos só leem o repositório:

```bash
python scripts/verificar.py 2>&1 | tail -1
python scripts/analisar.py conferir 2>&1 | tail -1
```

**Na tela:** a última linha de cada um, `... diretório(s) verificado(s), nenhum com reprovação.` e
`Todos os ... bloco(s) em dia.`

**Fala (Tiago):** "Os dados brutos estão no repositório. O verificar refaz a verificação de cada
cenário versionado, e o conferir garante que as tabelas dos documentos batem com os CSVs. A
correção não fica só na leitura do código: ela é conferida por invariantes sobre os logs dos Nós
reais."
