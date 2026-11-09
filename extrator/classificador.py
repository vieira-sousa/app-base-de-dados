"""Classificação do tipo de documento por palavras-chave (sem LLM).

Ordem de decisão:
  1. Nome do ficheiro / subpasta (ex.: "AP_2025.pdf", "analises/..", "cirurgia.pdf")
  2. Pontuação de palavras-chave no texto
"""
from __future__ import annotations

import re
from pathlib import Path

from .utils import normalizar

TIPOS = ("anatomia_patologica", "analises", "cirurgia")

# Pistas no nome do ficheiro / pasta (normalizado, sem acentos)
PISTAS_NOME = {
    "anatomia_patologica": [r"\bap\b", r"anatomia", r"patologica", r"histolog", r"biopsia"],
    "analises": [r"analise", r"\blab", r"patologia.?clinica", r"hemograma", r"bioquimica", r"\bea\b"],
    "cirurgia": [r"cirurg", r"operatori", r"\bro\b", r"\bop\b", r"protocolo", r"relato"],
}

# Palavras-chave no texto com peso
PALAVRAS = {
    "anatomia_patologica": {
        "anatomia patologica": 5, "relatorio anatomopatologico": 5, "exame macroscopico": 4, "macroscopia": 3,
        "microscopia": 3, "exame histologico": 3, "diagnostico histologico": 3,
        "peca operatoria": 3, "adenocarcinoma": 2, "carcinoma": 1, "ptnm": 4,
        "pt3": 2, "pt2": 2, "pt1": 2, "pt4": 2, "ganglios linfaticos": 2,
        "margem": 1, "imunohistoquimica": 2, "imuno-histoquimica": 2,
        "invasao perineural": 2, "invasao linfovascular": 2, "patologista": 2,
    },
    "analises": {
        "patologia clinica": 5, "analises clinicas": 5, "relatorio de resultados": 4, "valores de referencia": 4,
        "intervalo de referencia": 4, "colheita": 2, "hemograma": 3, "bioquimica": 3,
        "hemoglobina": 1, "leucocitos": 1, "plaquetas": 1, "creatinina": 1,
        "g/dl": 1, "mg/dl": 1, "x10^9/l": 1, "u/l": 1, "validado por": 1,
    },
    "cirurgia": {
        "relatorio operatorio": 5, "relatorio cirurgico": 5, "relato cirurgico": 5, "data intervencao": 4, "protocolo operatorio": 5,
        "descricao cirurgica": 4, "descricao da cirurgia": 4, "cirurgiao": 3,
        "ajudante": 2, "anestesista": 2, "anestesia geral": 2, "diagnostico pre-operatorio": 3,
        "intervencao realizada": 3, "procedimento": 1, "laparoscop": 1, "laparotomia": 2,
        "anastomose": 2, "trocar": 2, "pneumoperitoneu": 3, "encerramento": 1,
        "hemostase": 2, "perdas hematicas": 3, "drenagem": 1,
    },
}


def pontuacoes(texto: str) -> dict[str, int]:
    t = normalizar(texto)
    return {
        tipo: sum(peso * t.count(chave) if peso >= 3 else peso * min(t.count(chave), 3)
                  for chave, peso in palavras.items())
        for tipo, palavras in PALAVRAS.items()
    }


def classificar(texto: str, caminho: str | Path | None = None) -> tuple[str | None, dict[str, int]]:
    """Devolve (tipo, pontuações). tipo=None se não for possível decidir."""
    if caminho is not None:
        p = Path(caminho)
        # considera o nome do ficheiro e a pasta imediatamente acima
        nome = normalizar(f"{p.parent.name} {p.stem}".replace("_", " ").replace("-", " "))
        for tipo, padroes in PISTAS_NOME.items():
            if any(re.search(pad, nome) for pad in padroes):
                return tipo, {"_nome_ficheiro": 1}

    pts = pontuacoes(texto)
    melhor = max(pts, key=pts.get)
    ordenados = sorted(pts.values(), reverse=True)
    if ordenados[0] < 4 or ordenados[0] == ordenados[1]:
        return None, pts
    return melhor, pts
