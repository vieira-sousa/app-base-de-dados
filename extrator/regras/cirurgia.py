"""Regex para relatórios operatórios."""
from __future__ import annotations

import re

from ..utils import DATA_PADRAO, excerto, parse_data, parse_numero
from . import Candidato, regista

F = re.IGNORECASE


@regista("cirurgia.data_cirurgia")
def data_cirurgia(texto: str) -> Candidato | None:
    m = re.search(
        r"data\s+(?:da\s+|de\s+)?(?:cirurgia|interven[çc][ãa]o|opera[çc][ãa]o|procedimento)\s*[:.\-]?\s*" + DATA_PADRAO,
        texto, F,
    )
    if not m:
        m = re.search(r"(?:cirurgia|interven[çc][ãa]o)\s+(?:realizada\s+)?(?:em|a|no\s+dia)\s+" + DATA_PADRAO, texto, F)
    if m and (d := parse_data(m[1])):
        return Candidato(d, excerto(texto, m.start(), m.end(), 20))
    return None


# ---------------------------------------------------------------- abordagem
RE_CONVERSAO_NEG = re.compile(r"(?:sem|n[ãa]o\s+(?:houve|foi\s+necess[aá]ri[ao]))\s+(?:necessidade\s+de\s+)?convers[ãa]o", F)
RE_CONVERSAO = re.compile(
    r"convers[ãa]o\s+(?:para|a)\s+(?:cirurgia\s+)?(?:aberta|laparotomia|via\s+aberta)|"
    r"convertid[ao]\s+(?:para|a|em)\s+(?:cirurgia\s+)?(?:aberta|laparotomia)|"
    r"(?:optou-se|decidiu-se)\s+(?:pela|por)\s+convers[ãa]o|procedeu-se\s+[àa]\s+convers[ãa]o",
    F,
)
RE_ROBOTICA = re.compile(r"rob[óo]tic|da\s?vinci|assistid[ao]\s+por\s+robot", F)
RE_LAPAROSCOPICA = re.compile(r"laparosc[óo]pic|laparoscopia|pneumoperit|trocar|via\s+minimamente\s+invasiva", F)
RE_ABERTA = re.compile(r"laparotomia|incis[ãa]o\s+(?:mediana|subcostal|de\s+kocher|de\s+chevron|transversa)|via\s+aberta|cirurgia\s+aberta", F)
# "laparotomia" usada como incisão de extração (mini-laparotomia) não torna a cirurgia aberta
RE_MINI_LAPAROTOMIA = re.compile(r"mini-?\s?laparotomia|incis[ãa]o\s+de\s+(?:extra[çc][ãa]o|pfannenstiel)|extra[çc][ãa]o\s+da\s+pe[çc]a", F)


@regista("cirurgia.conversao")
def conversao(texto: str) -> Candidato | None:
    if m := RE_CONVERSAO_NEG.search(texto):
        return Candidato(False, excerto(texto, m.start(), m.end()))
    if m := RE_CONVERSAO.search(texto):
        return Candidato(True, excerto(texto, m.start(), m.end()))
    if RE_LAPAROSCOPICA.search(texto) or RE_ROBOTICA.search(texto):
        return Candidato(False, "(via minimamente invasiva, sem menção a conversão)")
    return None


@regista("cirurgia.abordagem")
def abordagem(texto: str) -> Candidato | None:
    conv = conversao(texto)
    if conv and conv.valor is True:
        return Candidato("convertida", conv.evidencia)
    if m := RE_ROBOTICA.search(texto):
        return Candidato("robotica", excerto(texto, m.start(), m.end()))
    if m := RE_LAPAROSCOPICA.search(texto):
        return Candidato("laparoscopica", excerto(texto, m.start(), m.end()))
    if m := RE_ABERTA.search(texto):
        if not RE_MINI_LAPAROTOMIA.search(texto[max(0, m.start() - 20): m.end() + 20]):
            return Candidato("aberta", excerto(texto, m.start(), m.end()))
    return None


@regista("cirurgia.abordagem_inicial")
def abordagem_inicial(texto: str) -> Candidato | None:
    """Via de abordagem inicial (intenção de tratar). A conversão é registada à parte."""
    if m := RE_ROBOTICA.search(texto):
        return Candidato("robotica", excerto(texto, m.start(), m.end()))
    if m := RE_LAPAROSCOPICA.search(texto):
        return Candidato("laparoscopica", excerto(texto, m.start(), m.end()))
    for m in RE_ABERTA.finditer(texto):
        if not RE_MINI_LAPAROTOMIA.search(texto[max(0, m.start() - 20): m.end() + 20]):
            return Candidato("aberta", excerto(texto, m.start(), m.end()))
    return None


# --------------------------------------------------------- perdas hemáticas
RE_PERDAS = re.compile(
    r"perdas?\s+(?:hem[aá]ticas?|sangu[ií]neas?)(?:\s+estimadas?)?(?:\s+(?:intra-?operat[óo]rias?|totais))?"
    r"[^0-9\n]{0,25}?(?:de\s+|em\s+|:\s*|~\s*|cerca\s+de\s+|aprox\w*\s+)?(\d+(?:[.,]\d+)?)\s*(ml|cc|l|litros?)\b",
    F,
)
RE_PERDAS_MINIMAS = re.compile(
    r"perdas?\s+(?:hem[aá]ticas?|sangu[ií]neas?)(?:\s+estimadas?)?\s*[:.\-]?\s*(?:foram\s+)?(?:m[ií]nimas|desprez[ií]veis|insignificantes|n[ãa]o\s+significativas|sem\s+perdas)",
    F,
)


@regista("cirurgia.perdas_hematicas_ml")
def perdas_hematicas_ml(texto: str) -> Candidato | None:
    if m := RE_PERDAS.search(texto):
        v = parse_numero(m[1])
        if m[2].lower().startswith("l"):
            v *= 1000
        return Candidato(int(round(v)), excerto(texto, m.start(), m.end()))
    if m := RE_PERDAS_MINIMAS.search(texto):
        return Candidato(0, excerto(texto, m.start(), m.end()), nota="descritas como mínimas — confirmar se a BD aceita 0")
    return None


# ------------------------------------------------------------------ duração
RE_DURACAO = re.compile(
    r"dura[çc][ãa]o(?:\s+(?:da\s+)?(?:cirurgia|interven[çc][ãa]o|procedimento))?\s*[:.\-]?\s*"
    r"(?:(\d{1,2})\s*(?:h|horas?)\s*(?:e\s*)?(\d{1,2})?\s*(?:m|min|minutos)?|(\d{2,3})\s*(?:min|minutos))",
    F,
)
RE_INICIO = re.compile(r"(?:hora\s+de\s+)?in[ií]cio(?:\s+da\s+cirurgia)?\s*[:.\-]?\s*(\d{1,2})\s*[:h]\s*(\d{2})", F)
RE_FIM = re.compile(r"(?:hora\s+de\s+)?(?:fim|t[ée]rmino|final)(?:\s+da\s+cirurgia)?\s*[:.\-]?\s*(\d{1,2})\s*[:h]\s*(\d{2})", F)


@regista("cirurgia.duracao_min")
def duracao_min(texto: str) -> Candidato | None:
    if m := RE_DURACAO.search(texto):
        if m[3]:
            minutos = int(m[3])
        else:
            minutos = int(m[1]) * 60 + int(m[2] or 0)
        if 0 < minutos < 24 * 60:
            return Candidato(minutos, excerto(texto, m.start(), m.end()))
    i, f = RE_INICIO.search(texto), RE_FIM.search(texto)
    if i and f:
        ini = int(i[1]) * 60 + int(i[2])
        fim = int(f[1]) * 60 + int(f[2])
        if fim < ini:
            fim += 24 * 60
        return Candidato(fim - ini, f"{i[0]} … {f[0]}", nota="calculado por hora de início/fim")
    return None


# ------------------------------------------------------- anastomose / estoma
RE_ANAST_MANUAL = r"manual|sutura\s+cont[ií]nua|pontos\s+separados|sero-?muscular|extramucos[ao]"
RE_ANAST_MECANICA = r"mec[aâ]nica|agrafador|stapler|endo-?gia|\bEEA\b|\bCDH\b|grampeador|circular\s+(?:de\s+)?\d{2}\s*mm"


@regista("cirurgia.anastomose_tecnica")
def anastomose_tecnica(texto: str) -> Candidato | None:
    # 1) técnica descrita na própria frase da anastomose (mais fiável)
    for m in re.finditer(r"anastomose[^.\n]{0,100}", texto, F):
        frase = m.group(0)
        man = re.search(RE_ANAST_MANUAL, frase, F)
        mec = re.search(RE_ANAST_MECANICA, frase, F)
        if man and mec:
            return Candidato("mista", excerto(texto, m.start(), m.end(), 10))
        if mec:
            return Candidato("mecanica", excerto(texto, m.start(), m.end(), 10))
        if man:
            return Candidato("manual", excerto(texto, m.start(), m.end(), 10))
    # 2) sem frase explícita: menções soltas — mais fraco, marcado para revisão
    if re.search(r"anastomose", texto, F):
        mec = re.search(r"anastomose\s+(?:foi\s+)?(?:realizada|confeccionada)\s+mecanicamente|" + RE_ANAST_MECANICA, texto, F)
        man = re.search(r"sutura\s+manual|manualmente", texto, F)
        if mec and not man:
            return Candidato("mecanica", excerto(texto, mec.start(), mec.end()),
                             nota="agrafador mencionado fora da frase da anastomose")
        if man and not mec:
            return Candidato("manual", excerto(texto, man.start(), man.end()),
                             nota="sutura manual mencionada fora da frase da anastomose")
    return None


RE_ESTOMA_NEG = re.compile(
    r"(?:sem|n[ãa]o\s+(?:se\s+)?(?:realiz|confeccion|construi|foi\s+necess[aá]ri)\w*)\s+(?:\w+\s+){0,3}(?:estoma|ileostomia|colostomia)", F)
RE_ESTOMA_FECHO = re.compile(r"(?:encerramento|reconstru[çc][ãa]o|restabelecimento|revers[ãa]o|fecho)\s+(?:\w+\s+){0,3}$", F)


def _mencao_estoma(texto: str, padrao: str, neg: list[tuple[int, int]]):
    for m in re.finditer(padrao, texto, F):
        if any(a <= m.start() < b for a, b in neg):
            continue
        if RE_ESTOMA_FECHO.search(texto[max(0, m.start() - 40): m.start()]):
            continue  # cirurgia de encerramento de estoma, não construção
        return m
    return None


@regista("cirurgia.estoma")
def estoma(texto: str) -> Candidato | None:
    negs = list(RE_ESTOMA_NEG.finditer(texto))
    spans = [(n.start(), n.end()) for n in negs]
    ileo = _mencao_estoma(texto, r"ileostomia", spans)
    colo = _mencao_estoma(texto, r"colostomia|opera[çc][ãa]o\s+de\s+hartmann", spans)
    if ileo:
        return Candidato("ileostomia", excerto(texto, ileo.start(), ileo.end()))
    if colo:
        return Candidato("colostomia", excerto(texto, colo.start(), colo.end()))
    if negs:
        return Candidato("nenhum", excerto(texto, negs[0].start(), negs[0].end()))
    return None
