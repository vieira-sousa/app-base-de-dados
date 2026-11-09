"""Extratores por expressões regulares.

Cada extrator recebe o texto bruto e devolve `Candidato | None`.
Registo: REGISTO["ap.pt"] = funcao
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Candidato:
    valor: Any
    evidencia: str          # excerto do texto que justifica o valor
    metodo: str = "regex"   # regex | llm
    ficheiro: str = ""
    nota: str = ""          # ex.: "inferido de 'margens livres'"
    verificado: bool = True  # para LLM: a citação foi encontrada no texto?


REGISTO: dict[str, Callable[[str], Candidato | None]] = {}


def regista(nome: str):
    def deco(fn):
        REGISTO[nome] = fn
        return fn
    return deco


# importa os módulos para popular o registo
from . import anatomia_patologica, cirurgia, colon, comum  # noqa: E402,F401
