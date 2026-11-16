"""Código do Trabalho 2: eleição de Líder em Anel e por Bully, Two-Phase Commit e a integração.

Na integração, o Líder do Anel coordena o 2PC. `RAIZ` é a pasta do projeto, derivada deste
arquivo, de onde saem `results/`, `docs/` e os arquivos do compose.
"""

from pathlib import Path

RAIZ: Path = Path(__file__).resolve().parent.parent
