"""Funções auxiliares: normalização de texto, datas e números em português."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

MESES = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
    "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
    "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}

# dd/mm/aaaa, dd-mm-aaaa, dd.mm.aaaa (ano com 2 ou 4 dígitos)
RE_DATA_DMY = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b")
# aaaa-mm-dd
RE_DATA_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# 12 de março de 2025
RE_DATA_EXTENSO = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zç]+)\s+(?:de\s+)?(\d{4})\b", re.IGNORECASE
)
# padrão textual combinado (útil para compor outras regex)
DATA_PADRAO = r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}[/.\-](?:\d{4}|\d{2})|\d{1,2}\s+de\s+[A-Za-zçÇ]+\s+(?:de\s+)?\d{4})"


def sem_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados (para comparação, não para posições)."""
    return re.sub(r"\s+", " ", sem_acentos(texto).lower()).strip()


def _construir_data(a: int, m: int, d: int) -> date | None:
    try:
        return date(a, m, d)
    except ValueError:
        return None


def parse_data(texto: str | None) -> date | None:
    """Converte a primeira data encontrada no texto para `date`."""
    if texto is None:
        return None
    if isinstance(texto, datetime):
        return texto.date()
    if isinstance(texto, date):
        return texto
    texto = str(texto).strip()
    if m := RE_DATA_ISO.search(texto):
        return _construir_data(int(m[1]), int(m[2]), int(m[3]))
    if m := RE_DATA_DMY.search(texto):
        ano = int(m[3])
        if ano < 100:
            ano += 2000 if ano < 70 else 1900
        return _construir_data(ano, int(m[2]), int(m[1]))
    if m := RE_DATA_EXTENSO.search(sem_acentos(texto)):
        mes = MESES.get(m[2].lower())
        if mes:
            return _construir_data(int(m[3]), mes, int(m[1]))
    return None


def parse_numero(texto: str | float | int | None) -> float | None:
    """'1.234,5' -> 1234.5 ; '3,2' -> 3.2 ; '3.2' -> 3.2"""
    if texto is None:
        return None
    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        return float(texto)
    s = str(texto).strip().replace(" ", "")
    s = s.lstrip("<>≤≥=~")
    if not s:
        return None
    if "," in s and "." in s:
        # o último separador é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def excerto(texto: str, inicio: int, fim: int, margem: int = 60) -> str:
    """Devolve um excerto do texto à volta de uma correspondência (para revisão)."""
    a = max(0, inicio - margem)
    b = min(len(texto), fim + margem)
    trecho = texto[a:b].replace("\n", " ")
    return ("…" if a > 0 else "") + re.sub(r"\s+", " ", trecho).strip() + ("…" if b < len(texto) else "")


def normalizar_id(valor) -> str:
    """Normaliza um identificador de doente (nº processo) para comparação."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    s = str(valor).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s
