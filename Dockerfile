# Imagem comum dos Nós do Trabalho 2.
#
# Uma única imagem Python serve tanto os Nós do Anel quanto os do 2PC: a imagem
# instala o requirements.txt e copia o pacote `src/`. Qual algoritmo um
# contêiner executa é escolhido pelo COMMAND do serviço no compose, não por uma
# imagem dedicada — por exemplo `python -m src.anel` para um Nó do Anel, ou o
# módulo correspondente do 2PC. Isso mantém o build único e reaproveitável pelos
# dois algoritmos (tarefas 4 e 6).

FROM python:3.12-slim

# Saída de log sem buffering e sem .pyc, para ver os eventos em tempo real e
# manter a imagem limpa.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Instala as dependências primeiro, para aproveitar o cache de camadas quando
# apenas o código-fonte muda.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código-fonte da aplicação. Os módulos de análise e scripts não são
# necessários dentro do contêiner de um Nó, mas copiar `src/` inteiro mantém os
# imports de `src.comum` resolvendo sem truques de PYTHONPATH.
COPY src/ ./src/

# Porta padrão dos servidores dos Nós; o compose pode sobrepor via env PORTA.
EXPOSE 8000

# Sem ENTRYPOINT fixo: o serviço do compose define o COMMAND (o módulo do
# algoritmo). O default abaixo sobe um Nó do Anel, útil para um `docker run`
# avulso, mas na prática o compose sempre informa o comando explícito.
CMD ["python", "-m", "src.anel"]
