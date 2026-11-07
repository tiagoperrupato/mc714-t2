"""Registro do ambiente (hardware, SO, versões) gravado em cada linha de resultado.

O enunciado exige registrar a Semente e o ambiente de execução em cada linha do
CSV (Requisitos 10.6, 13.6), porque contêineres numa única máquina são Nós
lógicos, não domínios de falha independentes, e resultados absolutos de máquinas
diferentes não se comparam diretamente. Aqui coletamos um retrato estável do
ambiente — SO, arquitetura, processador, número de CPUs, versão de Python e das
dependências principais — para carimbar junto com os dados.

A coleta é de leitura pura: não abre socket nem escreve em disco. Falhas pontuais
(por exemplo, uma dependência ausente) degradam para `"desconhecido"` em vez de
derrubar a bateria, para que o registro de ambiente nunca atrapalhe o
experimento.
"""

from __future__ import annotations

import os
import platform
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import metadata


@dataclass(frozen=True)
class Ambiente:
    """Retrato imutável do ambiente de execução de uma bateria.

    Os campos são todos escalares serializáveis, para entrarem direto numa linha
    de CSV. `pacotes` fica como string `nome==versao;...` compacta, de modo que a
    linha permaneça plana (uma coluna por informação), sem aninhar estrutura.
    """

    so: str
    so_versao: str
    arquitetura: str
    processador: str
    num_cpus: int
    python_versao: str
    pacotes: str

    def como_colunas(self) -> dict[str, str | int]:
        """Representação plana para compor as colunas de ambiente de cada linha."""
        return {f"amb_{chave}": valor for chave, valor in asdict(self).items()}


_PACOTES_DE_INTERESSE = ("fastapi", "uvicorn", "httpx", "pandas", "numpy", "matplotlib")


def _versao_pacote(nome: str) -> str:
    """Versão instalada de `nome`, ou `"ausente"` se a dependência não está presente."""
    try:
        return metadata.version(nome)
    except metadata.PackageNotFoundError:
        return "ausente"


@lru_cache(maxsize=1)
def coletar_ambiente() -> Ambiente:
    """Coleta o retrato do ambiente uma única vez por processo (memoizado).

    A memoização garante que todas as linhas de uma mesma bateria carreguem
    exatamente o mesmo ambiente, sem relê-lo do SO a cada réplica.
    """
    try:
        num_cpus = os.cpu_count() or 0
    except Exception:  # noqa: BLE001 - leitura de ambiente é best-effort
        num_cpus = 0
    pacotes = ";".join(f"{nome}=={_versao_pacote(nome)}" for nome in _PACOTES_DE_INTERESSE)
    return Ambiente(
        so=platform.system() or "desconhecido",
        so_versao=platform.release() or "desconhecido",
        arquitetura=platform.machine() or "desconhecido",
        processador=platform.processor() or "desconhecido",
        num_cpus=num_cpus,
        python_versao=platform.python_version(),
        pacotes=pacotes,
    )
