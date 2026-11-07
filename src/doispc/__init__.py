"""Two-Phase Commit: Máquina_de_Estados pura e Log_Duravel write-ahead.

Este pacote contém a lógica do 2PC separada em duas responsabilidades:

- `maquina`: funções puras sobre estado imutável que decidem as transições do
  Coordenador e do Participante a partir de uma Mensagem recebida, sem tocar em
  rede nem em disco (Requisito 6.1). Não importa FastAPI nem httpx.
- `log_duravel`: a interface do Log_Duravel write-ahead e suas implementações
  (em memória para testes, em arquivo para os Nós reais), que sustentam a
  durabilidade e a recuperação após reinício (Requisitos 6.4, 6.5, 6.6, 6.8).

Os apps FastAPI do Coordenador e do Participante (tarefa 6) são adaptadores
finos que chamam a Máquina_de_Estados, persistem pelo Log_Duravel na ordem
write-ahead e despacham as Mensagens de saída pelo Transporte.
"""
