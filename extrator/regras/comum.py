"""Extratores comuns a todos os documentos (identificação, datas)."""
from __future__ import annotations

import re

from ..utils import DATA_PADRAO, excerto, parse_data
from . import Candidato, regista

RE_PROCESSO = re.compile(
    r"(?:n\.?\s*º?\s*(?:de\s+)?processo|processo(?:\s+cl[ií]nico)?|n\.?\s*º?\s*proc\.?|n\.?\s*º?\s*sequencial|NSC|NUP)"
    r"\s*[:.\-]?\s*(\d{4,12})",
    re.IGNORECASE,
)


@regista("comum.processo")
def processo(texto: str) -> Candidato | None:
    m = RE_PROCESSO.search(texto)
    if not m:
        return None
    return Candidato(m[1], excerto(texto, m.start(), m.end(), 20))


RE_DATA_ROTULADA = re.compile(
    r"(?:data\s+(?:de\s+|da\s+)?(?:colheita|requisi[çc][ãa]o|recolha|relat[óo]rio|valida[çc][ãa]o|"
    r"emiss[ãa]o|cirurgia|interven[çc][ãa]o|resposta)|colheita|data)\s*[:.\-]?\s*" + DATA_PADRAO,
    re.IGNORECASE,
)


def data_documento(texto: str, preferir: tuple[str, ...] = ("colheita",)) -> Candidato | None:
    """Data mais provável do documento. Prefere rótulos indicados (ex. colheita)."""
    melhores = []
    for m in RE_DATA_ROTULADA.finditer(texto):
        d = parse_data(m[1])
        if not d:
            continue
        rotulo = m.group(0).lower()
        prioridade = 0 if any(p in rotulo for p in preferir) else 1
        melhores.append((prioridade, m.start(), d, m))
    if melhores:
        melhores.sort(key=lambda x: (x[0], x[1]))
        _, _, d, m = melhores[0]
        return Candidato(d, excerto(texto, m.start(), m.end(), 20))
    # fallback: primeira data plausível no texto
    m = re.search(DATA_PADRAO, texto)
    if m and (d := parse_data(m[1])):
        return Candidato(d, excerto(texto, m.start(), m.end(), 20), nota="data não rotulada")
    return None
