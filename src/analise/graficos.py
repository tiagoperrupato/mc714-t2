"""Único ponto de contato com o matplotlib, fixado no backend `Agg`.

`Agg` grava arquivo sem servidor gráfico, então as figuras saem por SSH ou num
contêiner, o que é essencial porque a análise roda headless e nunca sobe
contêiner.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (obrigatoriamente após use("Agg"))

# Legível num documento, com arquivo de algumas dezenas de KB.
DPI: int = 150


def nova_figura():
    """Cria uma figura e um eixo com o tamanho padrão do projeto."""
    return plt.subplots(figsize=(9.0, 6.0))


def salvar(fig, caminho: Path) -> None:
    """Grava a figura em PNG e em PDF e a fecha.

    O PNG vai para os markdowns e o PDF vetorial para o relatório em LaTeX. O PDF
    sai sem data de criação, para que regerar a figura não mude o arquivo. A
    figura é fechada porque o `pyplot` mantém as abertas num registro global.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(caminho, dpi=DPI)
    fig.savefig(caminho.with_suffix(".pdf"), metadata={"CreationDate": None})
    plt.close(fig)
