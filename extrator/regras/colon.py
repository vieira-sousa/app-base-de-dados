"""Regex específicas de neoplasia do cólon (hemicolectomia direita)."""
from __future__ import annotations

import re

from ..utils import excerto, parse_numero
from . import Candidato, regista
from .anatomia_patologica import _invasao

F = re.IGNORECASE
NEG = r"(?:sem|aus[eê]ncia\s+de|n[ãa]o\s+(?:se\s+)?(?:observ|identific|evidenci|objetiv|detet|detect|encontr)\w*)"


def _presenca(texto: str, termo: str, mencao_simples: bool = True) -> Candidato | None:
    """Presença/ausência de um achado. Usa _invasao (negações, 'termo: presente/ausente')
    e, em último caso, a simples menção do termo (marcada para revisão)."""
    c = _invasao(texto, termo, None)
    if c is not None:
        return c
    m = re.search(rf"{NEG}\s+(?:[\wÀ-ÿ-]+\s+){{0,4}}(?:{termo})", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    if mencao_simples and (m := re.search(termo, texto, F)):
        return Candidato(True, excerto(texto, m.start(), m.end()), nota="só menção do termo — confirmar")
    return None


# =============================================================== ANATOMIA PATOLÓGICA
HISTOLOGIA = [
    ("anel_sinete", r"carcinoma\s+(?:de\s+c[ée]lulas\s+)?(?:em\s+)?anel\s+de\s+sinete"),
    ("mucinoso", r"(?:adeno)?carcinoma\s+(?:mucinoso|mucoss?ecretor|col[óo]ide)"),
    ("medular", r"carcinoma\s+medular"),
    ("outro", r"adenocarcinoma\s+(?:serreado|micropapilar|adenoma-?like|tipo\s+adenoma)|carcinoma\s+adenoescamoso|"
              r"carcinoma\s+indiferenciado|carcinoma\s+sarcomat|neuroend[óo]crin|carcinoide"),
    ("adenocarcinoma_nos", r"adenocarcinoma"),
]


@regista("colon.histologia")
def histologia(texto: str) -> Candidato | None:
    # preferir a secção de diagnóstico, se existir
    m_diag = re.search(r"diagn[óo]stico|conclus[ãa]o", texto, F)
    secoes = [texto[m_diag.start():], texto] if m_diag else [texto]
    for sec in secoes:
        for valor, padrao in HISTOLOGIA:
            m = re.search(padrao, sec, F)
            if m:
                return Candidato(valor, excerto(sec, m.start(), m.end()))
    return None


RE_MUCINOSO = r"componente\s+mucinoso|diferencia[çc][ãa]o\s+mucinosa|(?:adeno)?carcinoma\s+mucinoso|\d{1,3}\s*%\s*(?:de\s+)?(?:componente\s+)?mucin"


@regista("colon.componente_mucinoso")
def componente_mucinoso(texto: str) -> Candidato | None:
    m = re.search(rf"{NEG}\s+(?:\w+\s+){{0,2}}(?:componente|diferencia[çc][ãa]o)\s+mucinos[ao]", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    m = re.search(RE_MUCINOSO, texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    return None


@regista("colon.percentagem_mucinosa")
def percentagem_mucinosa(texto: str) -> Candidato | None:
    for padrao in (r"(\d{1,3}(?:[.,]\d)?)\s*%\s*(?:de\s+)?(?:componente\s+|diferencia[çc][ãa]o\s+)?mucin",
                   r"(?:componente|diferencia[çc][ãa]o)\s+mucinos[ao][^.\n%]{0,40}?(\d{1,3}(?:[.,]\d)?)\s*%"):
        m = re.search(padrao, texto, F)
        if m:
            v = parse_numero(m[1])
            if v is not None and 0 <= v <= 100:
                return Candidato(v, excerto(texto, m.start(), m.end()))
    return None


@regista("colon.componente_anel_sinete")
def componente_anel_sinete(texto: str) -> Candidato | None:
    m = re.search(rf"{NEG}\s+(?:[\wÀ-ÿ]+\s+){{0,3}}anel\s+de\s+sinete", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    m = re.search(r"anel\s+de\s+sinete", texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    return None


RE_DEPOSITOS = r"dep[óo]sitos?\s+tumora(?:is|l)|dep[óo]sitos?\s+(?:de\s+)?(?:neoplasia|tumor)"


@regista("colon.depositos_tumorais")
def depositos_tumorais(texto: str) -> Candidato | None:
    m = re.search(rf"\b(\d{{1,2}})\s+(?:{RE_DEPOSITOS})", texto, F)
    if m:
        return Candidato(int(m[1]) > 0, excerto(texto, m.start(), m.end()))
    c = _presenca(texto, RE_DEPOSITOS, mencao_simples=False)
    if c is not None:
        return c
    m = re.search(r"(?<![A-Za-z0-9])p?N\s?1c(?![a-z0-9])", texto)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()), nota="inferido de pN1c")
    return None


@regista("colon.n_depositos_tumorais")
def n_depositos_tumorais(texto: str) -> Candidato | None:
    m = re.search(rf"\b(\d{{1,2}})\s+(?:{RE_DEPOSITOS})", texto, F)
    if not m:
        m = re.search(rf"(?:{RE_DEPOSITOS})[^.\n\d]{{0,30}}?\(?\s*(?:n\s*[=:]\s*)?(\d{{1,2}})\b(?!\s*(?:mm|cm|%))", texto, F)
    if m:
        return Candidato(int(m[1]), excerto(texto, m.start(), m.end()))
    return None


BUDDING = {"1": "baixo", "2": "intermedio", "3": "alto", "baixo": "baixo", "intermedio": "intermedio",
           "intermédio": "intermedio", "alto": "alto"}


@regista("colon.budding")
def budding(texto: str) -> Candidato | None:
    m = re.search(r"budding[^.\n]{0,50}?(?:\bBd\s?([123])\b|\b(baixo|interm[ée]dio|alto)\b)", texto, F)
    if not m:
        m = re.search(r"\bBd\s?([123])\b()", texto)
    if m:
        chave = (m[1] or m[2] or "").lower()
        return Candidato(BUDDING.get(chave), excerto(texto, m.start(), m.end()))
    return None


# ------------------------------------------------------------------- MMR / molecular
PERDA = r"perd(?:a|ida|ido)|aus[eê]ncia|ausente|negativ[oa]|n[ãa]o\s+(?:se\s+)?(?:observa|identifica|mant[eé]m)|deficiente"
RETIDO = r"mantid[ao]|manuten[çc][ãa]o|preservad[ao]|retid[ao]|conservad[ao]|positiv[oa]|normal|intact[ao]"
SEPARADOR_ORACOES = re.compile(r"[;.\n]|,\s*(?=com\s|mas\s|sem\s|e\s+(?:com|perda|express))|\s+com\s+(?=express|perda|manut|aus)", re.I)


def _estado_proteina(texto: str, proteina: str) -> Candidato | None:
    pos = 0
    for oracao in SEPARADOR_ORACOES.split(texto):
        inicio = texto.find(oracao, pos)
        pos = max(pos, inicio)
        m = re.search(rf"\b{proteina}\b", oracao, F)
        if not m:
            continue
        perda = [x.start() for x in re.finditer(PERDA, oracao, F)]
        retido = [x.start() for x in re.finditer(RETIDO, oracao, F)]
        if not perda and not retido:
            continue
        # a palavra de estado mais próxima da proteína decide
        d_perda = min((abs(p - m.start()) for p in perda), default=10**6)
        d_ret = min((abs(p - m.start()) for p in retido), default=10**6)
        valor = "perdido" if d_perda < d_ret else "retido"
        return Candidato(valor, excerto(texto, inicio, inicio + len(oracao), 10))
    return None


for _p in ("MLH1", "PMS2", "MSH2", "MSH6"):
    regista(f"colon.{_p.lower()}")(lambda texto, _p=_p: _estado_proteina(texto, _p))


@regista("colon.mmr")
def mmr(texto: str) -> Candidato | None:
    m = re.search(r"\b(p|d)MMR\b", texto)
    if m:
        return Candidato(f"{m[1].lower()}MMR", excerto(texto, m.start(), m.end()))
    m = re.search(r"(?:prote[íi]nas\s+(?:de\s+)?repara[çc][ãa]o|MMR)[^.\n]{0,60}?\b(proficiente|deficiente|defici[êe]ncia)", texto, F)
    if m:
        return Candidato("pMMR" if m[1].lower().startswith("profic") else "dMMR", excerto(texto, m.start(), m.end()))
    m = re.search(r"sem\s+(?:evid[êe]ncia\s+de\s+)?perda\s+de\s+express[ãa]o\s+(?:das\s+)?prote[íi]nas\s+(?:de\s+)?repara", texto, F)
    if m:
        return Candidato("pMMR", excerto(texto, m.start(), m.end()))
    return None


@regista("colon.msi")
def msi(texto: str) -> Candidato | None:
    m = re.search(r"\bMSI-?H\b|instabilidade\s+(?:de\s+)?microssat[ée]lites?\s+(?:de\s+)?alta|alta\s+instabilidade", texto, F)
    if m:
        return Candidato("MSI-H", excerto(texto, m.start(), m.end()))
    m = re.search(r"\bMSS\b|\bMSI-?L\b|microssat[ée]lites?\s+est[áa]ve(?:l|is)|estabilidade\s+(?:de\s+)?microssat|"
                  r"instabilidade\s+(?:de\s+)?microssat[ée]lites?\s+(?:de\s+)?baixa", texto, F)
    if m:
        return Candidato("MSS/MSI-L", excerto(texto, m.start(), m.end()))
    return None


WT = (r"wild[\s-]?type|\bWT\b|selvagem|n[ãa]o\s+mutad|sem\s+muta|aus[eê]ncia\s+de\s+muta|negativ|"
      r"n[ãa]o\s+(?:foram\s+|foi\s+)?(?:se\s+)?(?:detetad|detectad|identificad|encontrad|detet|detect|identific)\w*")
MUT = r"mutad[oa]|muta[çc][ãa]o\s+(?:\w+\s+){0,3}?(?:detetada|detectada|identificada|presente)|positiv|\b[GQAVK]\d{2,3}[A-Z]\b|\bp\.\w+\d+"


def _gene(texto: str, gene: str) -> Candidato | None:
    for m in re.finditer(rf"\b{gene}\b", texto, F):
        depois = texto[m.end(): m.end() + 90]
        corte = re.search(r"[.;\n]|\b(?:KRAS|NRAS|BRAF)\b", depois, F)
        janela = depois[: corte.start()] if corte else depois
        antes = texto[max(0, m.start() - 40): m.start()]
        if re.search(WT, janela, F):
            return Candidato("WT", excerto(texto, m.start(), m.end() + len(janela)))
        if re.search(MUT, janela):
            return Candidato("mutado", excerto(texto, m.start(), m.end() + len(janela)))
        # gene depois da frase: "Não foram detetadas mutações no KRAS" / "mutação do KRAS (G12D)"
        frase_antes = re.split(r"[.;\n]", antes)[-1]
        if re.search(r"muta[çc](?:[ãa]o|[õo]es)\s+(?:\w+\s+)?(?:n[oa]s?|d[oa]s?)\s*$", frase_antes, F):
            if re.search(WT, frase_antes, F):
                return Candidato("WT", excerto(texto, m.start() - len(frase_antes), m.end()))
            if not re.search(r"pesquisa|estudo|an[áa]lise", frase_antes, F):
                return Candidato("mutado", excerto(texto, m.start() - len(frase_antes), m.end()))
    return None


for _g in ("KRAS", "NRAS", "BRAF"):
    regista(f"colon.{_g.lower()}")(lambda texto, _g=_g: _gene(texto, _g))


@regista("colon.perfuracao_ap")
def perfuracao_ap(texto: str) -> Candidato | None:
    return _presenca(texto, r"perfura[çc][ãa]o(?:\s+tumoral|\s+(?:da\s+)?parede)?", mencao_simples=True)


LOCAL = [
    ("angulo_hepatico", r"[âa]ngulo\s+hep[áa]tico|flexura\s+hep[áa]tica"),
    ("cego", r"cego|ceco|v[áa]lvula\s+[íi]leo-?cecal"),
    ("ascendente", r"c[óo]lon\s+ascendente|ascendente"),
    ("transverso_proximal", r"(?:c[óo]lon\s+)?transverso"),
]
_LOCAIS = "|".join(p for _, p in LOCAL)
RE_LOCAL_CONTEXTO = [
    rf"topografia\s*[:\-]?\s*(?:c[óo]lon\s*,?\s*)?({_LOCAIS})",
    rf"(?:localiza[çc][ãa]o|sede)\s+(?:do\s+tumor\s*|tumoral\s*)?[:\-]?\s*(?:c[óo]lon\s*,?\s*)?({_LOCAIS})",
    rf"(?:adeno)?carcinoma\s+(?:\w+\s+){{0,3}}?(?:d[oa]|n[oa])\s+({_LOCAIS})",
    rf"(?:neoplasia|tumor|tumefa[çc][ãa]o|les[ãa]o|massa|ulcera[çc][ãa]o)[^.\n]{{0,60}}?(?:localizad|situad|sediad|centrad)\w*\s+(?:\w+\s+){{0,3}}?(?:n[oa]|a[o]?\s+n[íi]vel\s+d[oa])\s+({_LOCAIS})",
]


@regista("colon.localizacao")
def localizacao(texto: str) -> Candidato | None:
    for padrao in RE_LOCAL_CONTEXTO:
        m = re.search(padrao, texto, F)
        if m:
            achado = m[1]
            for valor, p in LOCAL:
                if re.fullmatch(p, achado, F):
                    nota = "transverso: confirmar se proximal" if valor == "transverso_proximal" else ""
                    return Candidato(valor, excerto(texto, m.start(), m.end()), nota=nota)
    return None


# ============================================================================ CIRURGIA
@regista("colon.tipo_colectomia")
def tipo_colectomia(texto: str) -> Candidato | None:
    m = re.search(r"(?:hemi)?colectomia\s+direita\s+alargada|alargada\s+ao\s+(?:c[óo]lon\s+)?transverso", texto, F)
    if m:
        return Candidato("direita_alargada", excerto(texto, m.start(), m.end()))
    m = re.search(r"hemicolectomia\s+direita|colectomia\s+direita", texto, F)
    if m:
        return Candidato("hemicolectomia_direita", excerto(texto, m.start(), m.end()))
    m = re.search(r"colectomia\s+(?:sub)?total|transversectomia|[íi]leo-?cecectomia|ileocolectomia|cecectomia", texto, F)
    if m:
        return Candidato("outro", excerto(texto, m.start(), m.end()))
    return None


@regista("colon.urgente")
def urgente(texto: str) -> Candidato | None:
    m = re.search(r"(?:tipo|car[áa]cter|regime)\s+(?:de\s+|da\s+)?(?:cirurgia|interven[çc][ãa]o|admiss[ãa]o)?\s*[:\-]?\s*"
                  r"(programad|urg|emerg|elet|elect)\w*", texto, F)
    if m:
        return Candidato(m[1].lower().startswith(("urg", "emerg")), excerto(texto, m.start(), m.end()))
    m = re.search(r"(?:cirurgia|interven[çc][ãa]o|laparotomia|colectomia)\s+(?:\w+\s+){0,2}?(?:de\s+)?(?:urg[êe]ncia|urgente|emergente|emerg[êe]ncia)|"
                  r"contexto\s+de\s+urg[êe]ncia|servi[çc]o\s+de\s+urg[êe]ncia", texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    m = re.search(r"(?:cirurgia|interven[çc][ãa]o)\s+(?:\w+\s+){0,2}?(?:programad|eletiv|electiv)\w*", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    return None


@regista("colon.anastomose_realizada")
def anastomose_realizada(texto: str) -> Candidato | None:
    m = re.search(rf"{NEG}\s+(?:\w+\s+){{0,2}}anastomose|(?:ileostomia|colostomia)\s+terminal|opera[çc][ãa]o\s+de\s+hartmann", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    m = re.search(r"anastomose\s+(?:[íi]leo|ileo|latero|l-l|topo|t-l|mec[aâ]nica|manual|intra|extra|isoperist|antiperist|prim[áa]ria)|"
                  r"(?:realiza|confecciona|efetua|executa)\w*\s+(?:\w+\s+){0,2}?anastomose", texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    return None


@regista("colon.anastomose_intracorporea")
def anastomose_intracorporea(texto: str) -> Candidato | None:
    m = re.search(r"intra-?\s?corp[óo]re[ao]", texto, F)
    if m:
        return Candidato(True, excerto(texto, m.start(), m.end()))
    m = re.search(r"extra-?\s?corp[óo]re[ao]|exterioriza[çc][ãa]o\s+(?:\w+\s+){0,3}?(?:anastomose|ansas)", texto, F)
    if m:
        return Candidato(False, excerto(texto, m.start(), m.end()))
    return None


@regista("colon.asa")
def asa(texto: str) -> Candidato | None:
    m = re.search(r"\bASA\s*(?:[:=]|classe|grau|score)?\s*(IV|III|II|I|[1-4])\b", texto)
    if not m:
        return None
    romano = {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(m[1], m[1])
    return Candidato(romano, excerto(texto, m.start(), m.end()))


@regista("colon.ascite")
def ascite(texto: str) -> Candidato | None:
    return _presenca(texto, r"ascite|l[íi]quido\s+asc[íi]tico|l[íi]quido\s+(?:livre\s+)?peritoneal")


@regista("colon.implantes_peritoneais")
def implantes_peritoneais(texto: str) -> Candidato | None:
    return _presenca(texto, r"carcinomatose(?:\s+peritoneal)?|implantes?\s+peritonea(?:is|l)|n[óo]dulos?\s+peritonea(?:is|l)|"
                            r"met[áa]stases?\s+peritonea(?:is|l)|doen[çc]a\s+peritoneal|implantes?\s+(?:tumorais|neopl[áa]sicos)")
