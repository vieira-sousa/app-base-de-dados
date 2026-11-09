"""Campos calculados a partir de outros campos e regras de "não aplicável".

Em campos.yaml:
    derivado: "estadio_ajcc(t_stage, n_stage, m_stage)"
    nao_aplicavel_se: ["approach == aberta", "anastomosis == false"]
"""
from __future__ import annotations

import re
from typing import Any, Callable

NAO_APLICAVEL = "nao_aplicavel"

# nome -> função(*valores) -> (valor, nota) | None
DERIVADOS: dict[str, Callable[..., tuple[Any, str] | None]] = {}


def derivado(nome: str):
    def deco(fn):
        DERIVADOS[nome] = fn
        return fn
    return deco


RE_CHAMADA = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(?:\(([^)]*)\))?\s*$", re.I)


def parse_chamada(expr: str) -> tuple[str, list[str]]:
    m = RE_CHAMADA.match(expr)
    if not m:
        raise ValueError(f"derivado inválido: {expr!r} (formato: funcao(campo1, campo2))")
    args = [a.strip() for a in (m[2] or "").split(",") if a.strip()]
    return m[1], args


def calcular(expr: str, valores: dict[str, Any]) -> tuple[Any, str] | None:
    nome, args = parse_chamada(expr)
    return DERIVADOS[nome](*[valores.get(a) for a in args])


# ------------------------------------------------------------- condições
RE_COND = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=|in)\s*(.+?)\s*$")


def _literal(s: str) -> Any:
    s = s.strip().strip("'\"")
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    return s


def validar_condicao(cond: str) -> None:
    if not RE_COND.match(cond):
        raise ValueError(f"condição inválida: {cond!r} (ex.: 'approach == aberta', 'x in [a, b]')")


def avaliar_condicao(cond: str, valores: dict[str, Any]) -> bool:
    m = RE_COND.match(cond)
    campo, op, rhs = m[1], m[2], m[3]
    v = valores.get(campo)
    if op == "in":
        opcoes = [_literal(x) for x in rhs.strip("[]").split(",")]
        return any(_igual(v, o) for o in opcoes)
    alvo = _literal(rhs)
    return _igual(v, alvo) if op == "==" else (v is not None and not _igual(v, alvo))


def _igual(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    return str(a).strip().lower() == str(b).strip().lower()


# ------------------------------------------------------------ derivados
@derivado("estadio_ajcc")
def estadio_ajcc(t, n, m=None):
    """Estádio AJCC 8.ª ed. (cólon) a partir de pT/pN/pM. Sem pM assume M0 (vai para revisão)."""
    if m and str(m).upper().startswith("M1"):
        return "IV", "calculado: pM1"
    if not t or not n:
        return None
    nota = f"calculado de {t} {n} (assume M0)"
    t, n = str(t).upper(), str(n).upper()
    if t in ("TX", "TIS", "T0") or n == "NX":
        return None
    if n.startswith("N1") or n.startswith("N2"):
        return "III", nota
    if n == "N0":
        if t in ("T1", "T2"):
            return "I", nota
        if t.startswith("T3") or t.startswith("T4"):
            return "II", nota
    return None


@derivado("mmr_de_proteinas")
def mmr_de_proteinas(*proteinas):
    if any(p == "perdido" for p in proteinas):
        return "dMMR", "calculado: perda de expressão de pelo menos uma proteína MMR"
    if len(proteinas) == 4 and all(p == "retido" for p in proteinas):
        return "pMMR", "calculado: expressão retida das 4 proteínas MMR"
    return None


@derivado("margem_de_r")
def margem_de_r(r):
    if r in ("R1", "R2"):
        return True, f"calculado de {r}"
    if r == "R0":
        return False, "calculado de R0"
    return None


@derivado("componente_mucinoso")
def componente_mucinoso(histologia):
    if histologia == "mucinoso":
        return True, "adenocarcinoma mucinoso"
    return None


@derivado("componente_anel_sinete")
def componente_anel_sinete(histologia):
    if histologia == "anel_sinete":
        return True, "carcinoma em anel de sinete"
    return None


@derivado("zero_se_ausente")
def zero_se_ausente(presente):
    if presente is False:
        return 0, "ausentes"
    return None
