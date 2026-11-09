"""Extração de valores de análises clínicas e escolha do valor por regra temporal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .regras import Candidato
from .regras.comum import data_documento
from .utils import excerto, normalizar, parse_numero

REGRAS = {
    "ultimo_antes_cirurgia", "primeiro_apos_cirurgia", "ultimo_apos_cirurgia",
    "maximo_apos_cirurgia", "minimo_apos_cirurgia", "ultimo", "primeiro", "maximo", "minimo",
}


@dataclass
class ValorAnalise:
    parametro: str
    valor: float            # já na unidade da BD
    comparador: str         # "", "<", ">"
    unidade: str            # unidade lida no relatório
    data: date | None
    ficheiro: str
    evidencia: str
    nota: str = ""


def _padrao_alias(alias: str) -> re.Pattern:
    nome = r"\s*".join(re.escape(p) for p in alias.split())
    return re.compile(
        r"^[ \t*]*(?:[A-Za-z]{1,6}\s*[-–.]\s*)?"        # prefixo de amostra opcional (S-, Soro -)
        + nome +
        r"(?![A-Za-zÀ-ÿ0-9])"                           # o alias termina aqui (Hb ≠ HbA1c)
        r"([ \t]*(?:\([^)\n]{0,30}\))?[ \t.:_*#%\-]*)"  # grupo 1: "(Hb)", pontilhado, #, % ...
        r"(?:(\d+(?:[.,]\d+)?)[ \t]*%[ \t]+(?=\d))?"      # grupo 2: "75.6 %" antes do valor absoluto
        r"([<>≤≥]?)[ \t]*(\d+(?:[.,]\d+)?)"             # grupos 3-4: comparador e valor
        r"[ \t]*((?:[xX][ \t]*10\S*)|(?:10[\^⁹¹³]\S*)|(?:[^\s\d]\S*))?",  # grupo 5: unidade
        re.IGNORECASE | re.MULTILINE,
    )


def norm_unidade(u: str | None) -> str:
    if not u:
        return ""
    s = normalizar(u).replace("μ", "u").replace("µ", "u").replace(" ", "").replace("*", "")
    s = s.replace("⁹", "^9").replace("³", "^3").replace("x10e", "x10^").replace("10e", "10^")
    return s.lstrip("x")


def _converter(valor: float, unidade_lida: str, p: dict) -> tuple[float, str]:
    """Converte para a unidade da BD. Devolve (valor, nota)."""
    destino = norm_unidade(p.get("unidade"))
    lida = norm_unidade(unidade_lida)
    if not destino:
        return valor, ""
    if not lida:
        return valor, f"unidade não indicada no relatório (BD em {p['unidade']}) — confirmar"
    if lida == destino:
        return valor, ""
    fatores = {norm_unidade(k): v for k, v in (p.get("conversoes") or {}).items()}
    if lida in fatores:
        novo = round(valor * fatores[lida], 4)
        return novo, f"convertido de {valor:g} {unidade_lida} para {novo:g} {p['unidade']}"
    return valor, f"unidade '{unidade_lida}' ≠ '{p['unidade']}' e sem conversão definida — confirmar"


def extrair_valores(texto: str, parametros: list[dict], ficheiro: str = "") -> list[ValorAnalise]:
    data_cand = data_documento(texto, preferir=("colheita", "requisi"))
    data_doc = data_cand.valor if data_cand else None
    resultados: list[ValorAnalise] = []
    for p in parametros:
        excluir = {norm_unidade(u) for u in p.get("excluir_unidades") or []}
        achado = None
        for alias in p["aliases"]:
            for m in _padrao_alias(alias).finditer(texto):
                unidade = re.sub(r"\s+", "", m[5] or "")
                entre = m[1] or ""
                if excluir and (norm_unidade(unidade) in excluir or any(e and e in norm_unidade(entre) for e in excluir)):
                    continue  # ex.: "Neutrófilos % 65" quando queremos o valor absoluto
                valor = parse_numero(m[4])
                if valor is None:
                    continue
                achado = (m, valor, unidade)
                break
            if achado:
                break
        if not achado:
            continue
        m, valor, unidade = achado
        convertido, nota = _converter(valor, unidade, p)
        intervalo = p.get("intervalo")
        if intervalo and not (intervalo[0] <= convertido <= intervalo[1]):
            nota = "; ".join(x for x in (nota, f"fora do intervalo plausível {intervalo} — confirmar") if x)
        resultados.append(ValorAnalise(
            parametro=p["nome"], valor=convertido, comparador={"≤": "<", "≥": ">"}.get(m[3], m[3]),
            unidade=unidade, data=data_doc, ficheiro=ficheiro,
            evidencia=excerto(texto, m.start(), m.end(), 15), nota=nota,
        ))
    return resultados


def escolher_valor(valores: list[ValorAnalise], regra: str, data_cirurgia: date | None,
                   janela_dias: int | None = None) -> Candidato | None:
    if regra not in REGRAS:
        raise ValueError(f"Regra de análise desconhecida: {regra}")
    if not valores:
        return None

    notas = []
    if regra.endswith("_cirurgia"):
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

    if regra in ("ultimo_antes_cirurgia", "ultimo", "ultimo_apos_cirurgia"):
        escolhido = max(elegiveis, key=lambda v: v.data or date.min)
    elif regra in ("primeiro_apos_cirurgia", "primeiro"):
        escolhido = min(elegiveis, key=lambda v: v.data or date.max)
    elif regra.startswith("maximo"):
        escolhido = max(elegiveis, key=lambda v: v.valor)
    else:
        escolhido = min(elegiveis, key=lambda v: v.valor)

    if escolhido.comparador:
        notas.append(f"valor reportado como '{escolhido.comparador}{escolhido.valor:g}'")
    if escolhido.nota:
        notas.append(escolhido.nota)
    data_txt = escolhido.data.isoformat() if escolhido.data else "s/ data"
    return Candidato(escolhido.valor, f"[{data_txt}] {escolhido.evidencia}",
                     ficheiro=escolhido.ficheiro, nota="; ".join(notas))
