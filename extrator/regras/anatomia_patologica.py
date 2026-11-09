"""Regex para relatórios de anatomia patológica (pTNM, margens, gânglios, etc.)."""
from __future__ import annotations

import re

from ..utils import excerto, parse_numero, sem_acentos
from . import Candidato, regista

F = re.IGNORECASE

# ------------------------------------------------------------------ pTNM
# Aceita: "pT3N1aM0", "ypT2 pN0", "pT4b N2a (5/22)", "TNM: pT3 pN1b pM1a", "pTis"
RE_T = re.compile(r"(?<![A-Za-z0-9])(y?r?y?)p\s?T\s?(is|x|0|[1-4][a-d]?)(?:\s?\((?:m|\d+)\))?", F)
# N e M só são procurados logo a seguir ao pT; aceitam forma colada ("pT3N1aM0")
_ANTES = r"(?:(?<=[0-9a-d)])|(?<![A-Za-z0-9]))"
RE_N = re.compile(_ANTES + r"(?:y?r?p)?\s?N\s?([xX]|0|[1-3][a-c]?)(?:\s?\((?:sn|i[+-]|mol[+-])\))?(?![a-z0-9])")
RE_M = re.compile(_ANTES + r"(?:y?r?p)?\s?M\s?([xX]|0|1[a-d]?)(?![a-z0-9])")
# janela após o pT onde procuramos N e M (mesma expressão TNM)
JANELA_TNM = 40


def _melhor_t(texto: str):
    """Escolhe a menção de pT mais fiável: preferir a que tem N a seguir."""
    ms = list(RE_T.finditer(texto))
    if not ms:
        return None
    for m in ms:
        if RE_N.search(texto[m.end(): m.end() + JANELA_TNM]):
            return m
    return ms[-1]  # a última menção costuma ser a do diagnóstico final


def _normaliza_cat(prefixo: str, valor: str) -> str:
    v = valor.lower()
    if v in ("is", "x"):
        return prefixo + ("is" if v == "is" else "X")
    return prefixo + v[0] + v[1:].lower()


@regista("ap.pt")
def pt(texto: str) -> Candidato | None:
    m = _melhor_t(texto)
    if not m:
        return None
    return Candidato(_normaliza_cat("T", m[2]), excerto(texto, m.start(), m.end(), 40))


@regista("ap.ptnm_prefixo")
def ptnm_prefixo(texto: str) -> Candidato | None:
    """'p', 'yp' (pós-neoadjuvância), 'rp' (recidiva) ou 'yrp'."""
    m = _melhor_t(texto)
    if not m:
        return None
    pref = "".join(sorted(set(m[1].lower()), key="yr".index))
    return Candidato(pref + "p", excerto(texto, m.start(), m.end(), 40))


@regista("ap.pn")
def pn(texto: str) -> Candidato | None:
    t = _melhor_t(texto)
    if t:
        m = RE_N.search(texto, t.end(), t.end() + JANELA_TNM)
        if m:
            return Candidato(_normaliza_cat("N", m[1]), excerto(texto, t.start(), m.end(), 40))
    m = re.search(r"(?<![A-Za-z0-9])y?p\s?N\s?(x|0|[1-3][a-c]?)(?![A-Za-z0-9])", texto, F)
    if m:
        return Candidato(_normaliza_cat("N", m[1]), excerto(texto, m.start(), m.end(), 40))
    return None


@regista("ap.pm")
def pm(texto: str) -> Candidato | None:
    t = _melhor_t(texto)
    if t:
        n = RE_N.search(texto, t.end(), t.end() + JANELA_TNM)
        inicio = n.end() if n else t.end()
        m = RE_M.search(texto, inicio, inicio + JANELA_TNM)
        if m and m[1].lower() != "x" and m[1] != "0":
            return Candidato(_normaliza_cat("M", m[1]), excerto(texto, t.start(), m.end(), 40))
    m = re.search(r"(?<![A-Za-z0-9])p\s?M\s?(1[a-d]?)(?![A-Za-z0-9])", texto, F)
    if m:
        return Candidato(_normaliza_cat("M", m[1]), excerto(texto, m.start(), m.end(), 40))
    return None


# ---------------------------------------------------------------- gânglios
# "(3/22)", "3 / 22 gânglios", "3 de 22 gânglios", "22 gânglios ... 3 com metástase"
RE_GANG_FRACAO = re.compile(r"(\d{1,3})\s*(?:/|de\s+|em\s+)\s*(\d{1,3})\b", F)
RE_GANG_PALAVRA = re.compile(r"g[aâ]ngli|linfonod", F)
RE_GANG_TOTAL = re.compile(
    r"(\d{1,3})\s+(?:g[aâ]nglios|linfonodos)(?:\s+linf[aá]ticos)?(?:\s+(?:isolados|identificados|examinados|observados|dissecados))?",
    F,
)
RE_GANG_POS = re.compile(
    r"\b(\d{1,3}|nenhum|um|dois|tr[eê]s)\b[^.\n]{0,40}?(?:com\s+)?(?:met[aá]stase|metastiza|invadid|neoplasia|positivo)",
    F,
)
NUM_EXT = {"nenhum": 0, "um": 1, "dois": 2, "tres": 3, "três": 3}


def _fracao_ganglios(texto: str):
    """Procura x/y perto da palavra 'gânglio' ou imediatamente após pN."""
    for m in RE_GANG_FRACAO.finditer(texto):
        pos, tot = int(m[1]), int(m[2])
        if pos > tot or tot == 0 or tot > 150:
            continue
        janela = texto[max(0, m.start() - 80): m.end() + 60]
        apos_pn = re.search(r"p?N\s?(?:x|0|[1-3][a-c]?)\s*\(?\s*$", texto[max(0, m.start() - 15): m.start()], F)
        if RE_GANG_PALAVRA.search(janela) or apos_pn:
            # excluir datas (dd/mm)
            if re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", texto[m.start(): m.end() + 5]):
                continue
            return pos, tot, m
    return None


@regista("ap.ganglios_examinados")
def ganglios_examinados(texto: str) -> Candidato | None:
    if f := _fracao_ganglios(texto):
        return Candidato(f[1], excerto(texto, f[2].start(), f[2].end()))
    m = RE_GANG_TOTAL.search(texto)
    if m:
        return Candidato(int(m[1]), excerto(texto, m.start(), m.end()))
    return None


@regista("ap.ganglios_positivos")
def ganglios_positivos(texto: str) -> Candidato | None:
    if f := _fracao_ganglios(texto):
        return Candidato(f[0], excerto(texto, f[2].start(), f[2].end()))
    total = RE_GANG_TOTAL.search(texto)
    if total:
        janela = texto[total.start(): total.end() + 150]
        m = RE_GANG_POS.search(janela, len(total[0]))
        if m:
            bruto = sem_acentos(m[1].lower())
            n = NUM_EXT.get(bruto, None) if not bruto.isdigit() else int(bruto)
            if n is not None:
                return Candidato(n, excerto(texto, total.start(), total.start() + m.end()))
        if re.search(r"(?:sem|livres?\s+de|negativos?\s+para)\s+(?:met[aá]stase|neoplasia)", janela, F):
            return Candidato(0, excerto(texto, total.start(), total.end() + 60), nota="inferido de 'sem metástases'")
    return None


# ----------------------------------------------------------------- margens
RE_R = re.compile(r"(?<![A-Za-z0-9])R\s?([012xX])(?![A-Za-z0-9])")
RE_R_CONTEXTO = re.compile(r"ress?e[cç]|margem|margens|classifica[çc][ãa]o\s+r|res[ií]du", F)
RE_MARGENS_LIVRES = re.compile(
    r"margens?\b[^.\n]{0,70}?\b(?:livres?|sem\s+(?:neoplasia|tumor|envolvimento|atingimento))\b", F)
RE_MARGEM_ENVOLVIDA = re.compile(
    r"margem\s+[^.\n]{0,40}?(?<!n[ãa]o\s)(?<!n[ãa]o\s\s)\b(?:envolvida|atingida|invadida|com\s+(?:neoplasia|tumor))", F)


@regista("ap.margem_r")
def margem_r(texto: str) -> Candidato | None:
    t = _melhor_t(texto)
    for m in RE_R.finditer(texto):
        janela = texto[max(0, m.start() - 60): m.end() + 30]
        na_expressao_tnm = t is not None and 0 <= m.start() - t.end() <= 60
        if RE_R_CONTEXTO.search(janela) or na_expressao_tnm:
            v = m[1].upper()
            return Candidato("Rx" if v == "X" else f"R{v}", excerto(texto, m.start(), m.end()))
    if m := RE_MARGEM_ENVOLVIDA.search(texto):
        return Candidato("R1", excerto(texto, m.start(), m.end()), nota="inferido de margem envolvida")
    if m := RE_MARGENS_LIVRES.search(texto):
        return Candidato("R0", excerto(texto, m.start(), m.end()), nota="inferido de 'margens livres'")
    return None


RE_MCR = re.compile(
    r"margem\s+(?:circunferencial|radial|mesorretal|mesorectal|CRM)[^0-9\n]{0,50}?(\d+(?:[.,]\d+)?)\s*(mm|cm)",
    F,
)


@regista("ap.margem_circunferencial_mm")
def margem_circunferencial_mm(texto: str) -> Candidato | None:
    m = RE_MCR.search(texto)
    if not m:
        return None
    v = parse_numero(m[1])
    if m[2].lower() == "cm":
        v = v * 10
    return Candidato(round(v, 1), excerto(texto, m.start(), m.end()))


# ------------------------------------------------------------------ tumor
RE_DIM = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:x\s*(\d+(?:[.,]\d+)?)\s*)?(?:x\s*(\d+(?:[.,]\d+)?)\s*)?(cm|mm)\b", F)
RE_CTX_TUMOR = re.compile(r"tumor|tumefa[çc][ãa]o|neoplasia|les[ãa]o|massa|ulcera[çc][ãa]o|maior\s+(?:dimens|eixo|di[aâ]metro)", F)


@regista("ap.tamanho_tumor_mm")
def tamanho_tumor_mm(texto: str) -> Candidato | None:
    c = tamanho_tumor_cm(texto)
    if c is not None and c.valor is not None:
        c.valor = round(c.valor * 10, 1)
    return c


@regista("ap.tamanho_tumor_cm")
def tamanho_tumor_cm(texto: str) -> Candidato | None:
    # preferir frase "maior dimensão/eixo"
    m = re.search(r"maior\s+(?:dimens[ãa]o|eixo|di[aâ]metro)[^0-9\n]{0,30}?(\d+(?:[.,]\d+)?)\s*(cm|mm)", texto, F)
    if m:
        v = parse_numero(m[1]) / (10 if m[2].lower() == "mm" else 1)
        return Candidato(round(v, 1), excerto(texto, m.start(), m.end()))
    for m in RE_DIM.finditer(texto):
        janela = texto[max(0, m.start() - 70): m.start()]
        depois = texto[m.end(): m.end() + 30]
        if (not RE_CTX_TUMOR.search(janela)
                or re.search(r"dist|margem|afast|comprimento", janela[-35:], F)
                or re.search(r"^\s*(?:d[aoe]s?\s+)?(?:margem|bordo|limite|dist[aâ]ncia|comprimento|linha|anel|v[aá]lvula|jun[çc][ãa]o)", depois, F)):
            continue
        dims = [parse_numero(x) for x in (m[1], m[2], m[3]) if x]
        if not dims:
            continue
        v = max(dims) / (10 if m[4].lower() == "mm" else 1)
        return Candidato(round(v, 1), excerto(texto, m.start(), m.end()))
    return None


# ------------------------------------------------------------------- grau
GRAU_MAPA = [
    (r"\bG\s?([1-4])\b", None),
    (r"bem\s+diferenciad", "G1"),
    (r"moderadamente\s+diferenciad", "G2"),
    (r"pouco\s+diferenciad|mal\s+diferenciad", "G3"),
    (r"indiferenciad", "G4"),
    (r"(?:de\s+)?baixo\s+grau", "baixo_grau"),
    (r"(?:de\s+)?alto\s+grau", "alto_grau"),
]


@regista("ap.grau_diferenciacao")
def grau_diferenciacao(texto: str) -> Candidato | None:
    for padrao, valor in GRAU_MAPA:
        for m in re.finditer(padrao, texto, F):
            if valor is None:
                # G1–G4 só se houver contexto de grau (evita "G2" de outras coisas)
                janela = texto[max(0, m.start() - 50): m.end() + 10]
                if not re.search(r"grau|diferencia|histol", janela, F):
                    continue
                return Candidato(f"G{m[1]}", excerto(texto, m.start(), m.end()))
            # "displasia de alto grau" não é o grau do carcinoma
            if valor in ("baixo_grau", "alto_grau") and re.search(r"displasia|neoplasia\s+intraepitelial|PanIN|NIC", texto[max(0, m.start() - 30): m.start()], F):
                continue
            return Candidato(valor, excerto(texto, m.start(), m.end()))
    return None


# ---------------------------------------------------- invasões (sim / não)
PRESENTE = r"(?:presente|identificad[ao]|observad[ao]|positiv[ao]|sim|evidente|focal)"
AUSENTE = r"(?:ausente|n[ãa]o\s+(?:se\s+)?(?:identificad|observad|evidenciad)\w*|negativ[ao]|n[ãa]o|sem|inexistente)"


def _invasao(texto: str, termo: str, codigo: str | None) -> Candidato | None:
    """Detecta presença/ausência de uma invasão. termo=regex, codigo ex. 'L' (L0/L1)."""
    termo = f"(?:{termo})"
    # 1) códigos L0/L1, V0/V1/V2, Pn0/Pn1
    if codigo:
        m = re.search(rf"(?<![A-Za-z0-9]){codigo}\s?([0-2])(?![A-Za-z0-9])", texto)
        if m:
            return Candidato(m[1] != "0", excerto(texto, m.start(), m.end()))
    # 2) "sem invasão X" / "ausência de invasão X"
    m = re.search(rf"(?:sem|aus[eê]ncia\s+de|n[ãa]o\s+(?:se\s+)?(?:identifica|observa|evidencia)[a-z]*)\s+(?:\w+\s+){{0,3}}{termo}", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    # 3) "invasão X: presente/ausente" (até 40 caracteres depois)
    for m in re.finditer(termo, texto, F):
        depois = texto[m.end(): m.end() + 40]
        a = re.search(AUSENTE, depois, F)
        p = re.search(PRESENTE, depois, F)
        if a and (not p or a.start() < p.start()):
            return Candidato(False, excerto(texto, m.start(), m.end() + a.end()))
        if p:
            return Candidato(True, excerto(texto, m.start(), m.end() + p.end()))
    # 4) "com invasão X" / "observa-se invasão X"
    m = re.search(rf"(?:com|observa-se|identifica-se|presen[çc]a\s+de)\s+(?:\w+\s+){{0,2}}{termo}", texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    return None


@regista("ap.invasao_linfovascular")
def invasao_linfovascular(texto: str) -> Candidato | None:
    return _invasao(texto, r"invas[ãa]o\s+(?:linfo-?vascular|linf[aá]tica|vascular\s+linf[aá]tica|angiolinf[aá]tica)|\bLVI\b", "L")


@regista("ap.invasao_venosa")
def invasao_venosa(texto: str) -> Candidato | None:
    return _invasao(texto, r"invas[ãa]o\s+(?:venosa|vascular\s+venosa)(?:\s+(?:intra|extra)mural)?", "V")


@regista("ap.invasao_perineural")
def invasao_perineural(texto: str) -> Candidato | None:
    return _invasao(texto, r"invas[ãa]o\s+peri-?neural|infiltra[çc][ãa]o\s+peri-?neural|\bPNI\b", "Pn")


# ------------------------------------------------- regressão pós-neoadjuvância
RE_TRG = re.compile(
    r"(Mandard|Dworak|Ryan|Becker|TRG|grau\s+de\s+regress[ãa]o(?:\s+tumoral)?)"
    r"[^0-9\n]{0,30}?(?:TRG\s*)?([0-5][a-c]?)\b",
    F,
)


@regista("ap.grau_regressao")
def grau_regressao(texto: str) -> Candidato | None:
    m = RE_TRG.search(texto)
    if not m:
        return None
    sistema = m[1]
    if sistema.lower().startswith("grau"):
        janela = texto[m.start(): m.end() + 60]
        s = re.search(r"Mandard|Dworak|Ryan|Becker|CAP|AJCC", janela, F)
        sistema = s[0] if s else "TRG"
    sistema = sistema if sistema.upper() in ("TRG", "CAP", "AJCC") else sistema.capitalize()
    return Candidato(f"{sistema} {m[2]}", excerto(texto, m.start(), m.end()))
