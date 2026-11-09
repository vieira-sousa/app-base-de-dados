"""Extração de valores de análises clínicas e escolha do valor por regra temporal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .regras import Candidato
from .regras.comum import data_documento
from .utils import excerto, normalizar, parse_numero

REGRAS = {
    "ultimo_antes_cirurgia", "primeiro_apos_cirurgia", "maximo_apos_cirurgia",
    "minimo_apos_cirurgia", "ultimo", "primeiro", "maximo", "minimo",
}


@dataclass
class ValorAnalise:
    parametro: str
    valor: float
    comparador: str     # "", "<", ">"
    unidade: str
    data: date | None
    ficheiro: str
    evidencia: str


def _padrao_alias(alias: str) -> re.Pattern:
    partes = [re.escape(p) for p in alias.split()]
    nome = r"\s*".join(partes)
    return re.compile(
        r"^[ \t*]*(?:[A-Za-z]{1,6}\s*[-–.]\s*)?"        # prefixo de amostra opcional (S-, Soro -)
        + nome +
        r"(?![A-Za-zÀ-ÿ0-9])"                           # o alias termina aqui (Hb ≠ HbA1c)
        r"[ \t]*(?:\([^)\n]{0,30}\))?"                  # "(Hb)" opcional
        r"[ \t.:_*\-]*"                                 # separadores / pontilhado / flags
        r"([<>≤≥]?)[ \t]*(\d+(?:[.,]\d+)?)"             # valor
        r"[ \t]*([^\s\d][^\s]*)?",                       # unidade (opcional)
        re.IGNORECASE | re.MULTILINE,
    )


def _norm_unidade(u: str) -> str:
    return normalizar(u).replace("μ", "u").replace("µ", "u").replace(" ", "").replace("*", "")


def extrair_valores(texto: str, parametros: list[dict], ficheiro: str = "") -> list[ValorAnalise]:
    data_cand = data_documento(texto, preferir=("colheita", "requisi"))
    data_doc = data_cand.valor if data_cand else None
    resultados: list[ValorAnalise] = []
    for p in parametros:
        vistos: set[int] = set()
        for alias in p["aliases"]:
            for m in _padrao_alias(alias).finditer(texto):
                if m.start() in vistos:
                    continue
                vistos.add(m.start())
                valor = parse_numero(m[2])
                if valor is None:
                    continue
                comparador = {"≤": "<", "≥": ">"}.get(m[1], m[1])
                resultados.append(ValorAnalise(
                    parametro=p["nome"], valor=valor, comparador=comparador,
                    unidade=(m[3] or "").strip(), data=data_doc, ficheiro=ficheiro,
                    evidencia=excerto(texto, m.start(), m.end(), 15),
                ))
                break  # um valor por alias por documento (primeira linha)
            if any(r.parametro == p["nome"] and r.ficheiro == ficheiro for r in resultados):
                break
    return resultados


def escolher_valor(valores: list[ValorAnalise], regra: str, data_cirurgia: date | None,
                   janela_dias: int | None = None, unidade_esperada: str | None = None) -> Candidato | None:
    if regra not in REGRAS:
        raise ValueError(f"Regra de análise desconhecida: {regra}")
    if not valores:
        return None

    notas = []
    relativos = regra.endswith("_cirurgia")
    if relativos:
        if data_cirurgia is None:
            return Candidato(None, "", nota=f"{len(valores)} valor(es) encontrados mas sem data de cirurgia para aplicar '{regra}'")
        sem_data = [v for v in valores if v.data is None]
        if sem_data:
            notas.append(f"{len(sem_data)} valor(es) sem data ignorados")
        com_data = [v for v in valores if v.data is not None]
        if regra == "ultimo_antes_cirurgia":
            elegiveis = [v for v in com_data if v.data <= data_cirurgia]
        else:
            elegiveis = [v for v in com_data if v.data > data_cirurgia]
        if janela_dias is not None:
            elegiveis = [v for v in elegiveis if abs((v.data - data_cirurgia).days) <= janela_dias]
    else:
        elegiveis = list(valores)

    if not elegiveis:
        return Candidato(None, "", nota="; ".join(notas + [f"nenhum valor cumpre a regra '{regra}'"]))

    ordem_data = lambda v: v.data or date.min  # noqa: E731
    if regra in ("ultimo_antes_cirurgia", "ultimo"):
        escolhido = max(elegiveis, key=ordem_data)
    elif regra in ("primeiro_apos_cirurgia", "primeiro"):
        escolhido = min(elegiveis, key=lambda v: v.data or date.max)
    elif regra.startswith("maximo"):
        escolhido = max(elegiveis, key=lambda v: v.valor)
    else:
        escolhido = min(elegiveis, key=lambda v: v.valor)

    if escolhido.comparador:
        notas.append(f"valor reportado como '{escolhido.comparador}{escolhido.valor:g}'")
    if unidade_esperada and escolhido.unidade and _norm_unidade(escolhido.unidade) != _norm_unidade(unidade_esperada):
        notas.append(f"unidade '{escolhido.unidade}' ≠ esperada '{unidade_esperada}'")
    data_txt = escolhido.data.isoformat() if escolhido.data else "s/ data"
    return Candidato(
        escolhido.valor,
        f"[{data_txt}] {escolhido.evidencia}",
        ficheiro=escolhido.ficheiro,
        nota="; ".join(notas),
    )
