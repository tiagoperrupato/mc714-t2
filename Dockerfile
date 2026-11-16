# Imagem única dos Nós do T2: instala o requirements.txt e copia o pacote src/. O command de cada
# serviço do compose escolhe o algoritmo: `python -m src.anel` para um Nó do Anel,
# `python -m src.doispc` para o Coordenador ou um Participante e `python -m src.integracao` para um
# candidato da integração, que roda um Nó do Anel e um Coordenador no mesmo processo.

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

# Copia o código-fonte da aplicação. Os módulos de análise e de experimentos não são
# necessários dentro do contêiner de um Nó, mas copiar `src/` inteiro mantém os
# imports de `src.comum` resolvendo sem truques de PYTHONPATH.
COPY src/ ./src/

# Porta padrão dos servidores dos Nós. O ambiente do serviço no compose pode trocá-la.
EXPOSE 8000

# Sem ENTRYPOINT fixo: o serviço do compose define o COMMAND (o módulo do
# algoritmo). O default abaixo sobe um Nó do Anel, útil para um `docker run`
# avulso, mas na prática o compose sempre informa o comando explícito.
CMD ["python", "-m", "src.anel"]
