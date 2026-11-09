"""Motor híbrido: junta regex + LLM por doente, deteta discordâncias e prepara revisão."""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .analises import ValorAnalise, escolher_valor, extrair_valores
from .classificador import classificar
from .derivados import NAO_APLICAVEL, avaliar_condicao, calcular, parse_chamada
from .esquema import Campo, ConfigCampos, coagir
from .llm import ClienteLLM
from .pdf_texto import Documento, ler_documento, listar_documentos
from .regras import REGISTO, Candidato
from .regras.anatomia_patologica import RE_T
from .regras.comum import data_documento, processo
from .utils import excerto, normalizar, normalizar_id

log = logging.getLogger(__name__)

OK, REVER, SEM_DADOS = "ok", "rever", "sem_dados"


@dataclass
class ResultadoCampo:
    campo: str
    valor: Any = None
    estado: str = SEM_DADOS
    fonte: str = ""             # regex | llm | regex+llm
    valor_regex: Any = None
    valor_llm: Any = None
    evidencia: str = ""
    ficheiro: str = ""
    notas: list[str] = field(default_factory=list)


@dataclass
class ResultadoDoente:
    id_doente: str
    documentos: list[dict] = field(default_factory=list)
    campos: dict[str, ResultadoCampo] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict:
        def conv(v):
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return conv({
            "id_doente": self.id_doente,
            "documentos": self.documentos,
            "avisos": self.avisos,
            "campos": {k: asdict(v) for k, v in self.campos.items()},
        })


# ----------------------------------------------------------- auxiliares
def _regex_campo(c: Campo, texto: str) -> Candidato | None:
    if c.regex:
        return REGISTO[c.regex](texto)
    m = re.search(c.padrao, texto, re.IGNORECASE | re.MULTILINE)
    if not m or m.group(1) is None:
        return None
    bruto = m.group(1).strip()
    valor: Any = bruto
    if c.mapa:
        chave = normalizar(bruto)
        mapa_norm = {normalizar(str(k)): v for k, v in c.mapa.items()}
        valor = mapa_norm.get(chave, bruto)
    return Candidato(valor, excerto(texto, m.start(), m.end()))


def _iguais(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 0.01
    return normalizar(str(a)) == normalizar(str(b))


def _ordenar_documentos(docs: list[Documento], tipo: str) -> list[Documento]:
    """Documento principal primeiro.

    - anatomia patológica: peça com pTNM antes de biópsias; depois o mais recente
    - cirurgia: o mais antigo primeiro (cirurgia índice antes de reintervenções)
    - restantes: mais recente primeiro
    """
    def data(d: Documento) -> date:
        c = data_documento(d.texto, preferir=("cirurgia", "interven", "relat"))
        return c.valor if c else date.min

    if tipo == "anatomia_patologica":
        return sorted(docs, key=lambda d: (RE_T.search(d.texto) is None, -data(d).toordinal()))
    if tipo == "cirurgia":
        return sorted(docs, key=lambda d: data(d) if data(d) != date.min else date.max)
    return sorted(docs, key=lambda d: -data(d).toordinal())


def _fundir(c: Campo, r: Candidato | None, l: Candidato | None) -> ResultadoCampo:
    """Combina o candidato regex e o LLM de UM documento."""
    res = ResultadoCampo(campo=c.nome)
    vr = vl = None
    if r is not None:
        vr, erro = coagir(c.tipo, r.valor, c.opcoes)
        if erro:
            res.notas.append(f"regex: {erro}")
        if r.nota:
            res.notas.append(f"regex: {r.nota}")
    if l is not None:
        vl, erro = coagir(c.tipo, l.valor, c.opcoes)
        if erro:
            res.notas.append(f"LLM: {erro}")
        if l.nota:
            res.notas.append(f"LLM: {l.nota}")
    res.valor_regex, res.valor_llm = vr, vl

    if vr is None and vl is None:
        res.estado = SEM_DADOS
        return res

    if vr is not None and vl is not None:
        if _iguais(vr, vl):
            res.valor, res.fonte, res.estado = vr, "regex+llm", OK
            res.evidencia = r.evidencia or l.evidencia
            return res
        venc, cand, fonte = (vl, l, "llm") if c.prioridade == "llm" else (vr, r, "regex")
        res.valor, res.fonte, res.evidencia, res.estado = venc, fonte, cand.evidencia, REVER
        res.notas.insert(0, f"DISCORDÂNCIA regex={vr!r} vs LLM={vl!r}")
        return res

    if vr is not None:
        res.valor, res.fonte, res.evidencia = vr, "regex", r.evidencia
        inferido = bool(r.nota)
    else:
        res.valor, res.fonte, res.evidencia = vl, "llm", l.evidencia
        inferido = not l.verificado
    res.estado = REVER if (inferido or res.notas) else OK
    return res


# ----------------------------------------------------------- processamento
def carregar_correspondencia(caminho: str | Path) -> dict[str, set[str]]:
    """Tabela local patient_id -> nº(s) de processo, só para verificar que os PDFs são do doente certo.

    CSV com cabeçalho 'patient_id;processo' (ou vírgula). NUNCA vai para a BD nem para o git.
    """
    import csv

    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    texto = caminho.read_text(encoding="utf-8-sig")
    dialeto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,\t")
    mapa: dict[str, set[str]] = {}
    for linha in csv.DictReader(texto.splitlines(), dialect=dialeto):
        chaves = {k.strip().lower(): v for k, v in linha.items() if k}
        pid, proc = normalizar_id(chaves.get("patient_id")), normalizar_id(chaves.get("processo"))
        if pid and proc:
            mapa.setdefault(pid, set()).add(proc.lstrip("0"))
    return mapa


def processar_doente(
    pasta: str | Path,
    cfg: ConfigCampos,
    llm: ClienteLLM | None = None,
    usar_ocr: bool = True,
    progresso: Callable[[str], None] | None = None,
    correspondencia: dict[str, set[str]] | None = None,
) -> ResultadoDoente:
    pasta = Path(pasta)
    id_doente = normalizar_id(pasta.name)
    res = ResultadoDoente(id_doente=id_doente)
    info = progresso or (lambda s: None)

    # 1. ler e classificar
    processos_vistos: dict[str, list[str]] = {}
    por_tipo: dict[str, list[Documento]] = {"anatomia_patologica": [], "analises": [], "cirurgia": []}
    for caminho in listar_documentos(pasta):
        info(f"{id_doente}: a ler {caminho.name}")
        try:
            doc = ler_documento(caminho, usar_ocr=usar_ocr)
        except Exception as e:
            res.avisos.append(f"{caminho.name}: erro a ler ({e})")
            continue
        doc.tipo, pts = classificar(doc.texto, caminho)
        res.documentos.append({"ficheiro": doc.nome, "tipo": doc.tipo, "metodo_texto": doc.metodo,
                               "caracteres": len(doc.texto), "avisos": doc.avisos})
        for a in doc.avisos:
            res.avisos.append(f"{doc.nome}: {a}")
        if doc.tipo is None:
            res.avisos.append(f"{doc.nome}: tipo de documento não reconhecido {pts} — renomeie com prefixo AP_/analises_/cirurgia_")
            continue
        p = processo(doc.texto)
        if p:
            processos_vistos.setdefault(normalizar_id(p.valor).lstrip("0"), []).append(doc.nome)
        por_tipo[doc.tipo].append(doc)

    # verificação de identidade (o nº de processo nunca é guardado nos resultados)
    if len(processos_vistos) > 1:
        res.avisos.append("⚠ PDFs com números de processo DIFERENTES nesta pasta: "
                          + " | ".join(", ".join(f) for f in processos_vistos.values())
                          + ". Confirmar se são todos do mesmo doente!")
    if correspondencia is not None:
        esperados = correspondencia.get(id_doente)
        if esperados is None:
            res.avisos.append(f"{id_doente} não está na tabela de correspondência — identidade não verificada.")
        else:
            for proc, fichs in processos_vistos.items():
                if proc not in esperados:
                    res.avisos.append(f"⚠ {', '.join(fichs)}: nº processo não corresponde a {id_doente} "
                                      "na tabela de correspondência. Documento de outro doente?")

    if not res.documentos:
        res.avisos.append("Nenhum PDF/TXT encontrado na pasta.")

    for tipo in por_tipo:
        por_tipo[tipo] = _ordenar_documentos(por_tipo[tipo], tipo)
        if tipo == "cirurgia" and len(por_tipo[tipo]) > 1:
            res.avisos.append(f"{len(por_tipo[tipo])} relatórios operatórios — usado '{por_tipo[tipo][0].nome}' "
                              "como cirurgia índice (o mais antigo). Confirmar.")

    # 2. campos de texto (AP, cirurgia)
    for tipo, docs in por_tipo.items():
        campos = [c for c in cfg.campos if c.documento == tipo]
        if not campos:
            continue
        por_doc: list[tuple[Documento, dict[str, ResultadoCampo]]] = []
        for doc in docs:
            # regex primeiro (instantâneo)
            regex_cands: dict[str, Candidato | None] = {}
            for c in campos:
                if c.usa_regex:
                    try:
                        regex_cands[c.nome] = _regex_campo(c, doc.texto)
                    except Exception as e:  # um extrator com erro não deve parar o doente
                        res.avisos.append(f"{doc.nome}: regex '{c.nome}' falhou ({e})")
            # LLM: campos 'llm' sempre; campos 'hibrido' conforme llm_em_hibridos
            so_se_falhar = llm is not None and llm.cfg.llm_em_hibridos == "se_regex_falhar"
            campos_llm = [
                c for c in campos
                if c.metodo == "llm" or (c.metodo == "hibrido" and not (
                    so_se_falhar and regex_cands.get(c.nome) is not None
                    and regex_cands[c.nome].valor is not None))
            ]
            llm_cands: dict[str, Candidato] = {}
            if llm and campos_llm:
                info(f"{id_doente}: LLM em {doc.nome} ({len(campos_llm)} campos)")
                try:
                    llm_cands = llm.extrair(doc.texto, tipo, campos_llm, ficheiro=doc.nome)
                except Exception as e:
                    res.avisos.append(f"{doc.nome}: LLM falhou ({e.__class__.__name__}: {e})")
            resultados = {}
            for c in campos:
                rc = _fundir(c, regex_cands.get(c.nome), llm_cands.get(c.nome))
                rc.ficheiro = doc.nome
                if llm is None and c.metodo == "llm":
                    rc.notas.append("LLM desativado")
                resultados[c.nome] = rc
            por_doc.append((doc, resultados))

        for c in campos:
            final = None
            for _doc, resultados in por_doc:
                if resultados[c.nome].valor is not None:
                    final = resultados[c.nome]
                    break
            if final is None:
                final = ResultadoCampo(campo=c.nome)
                if llm is None and c.metodo == "llm":
                    final.notas.append("LLM desativado")
            else:
                principal = por_doc[0][0].nome
                if final.ficheiro != principal:
                    final.notas.append(f"valor obtido de documento secundário ({final.ficheiro})")
                outros = {
                    resultados[c.nome].ficheiro: resultados[c.nome].valor
                    for _d, resultados in por_doc
                    if resultados[c.nome].valor is not None and not _iguais(resultados[c.nome].valor, final.valor)
                }
                if outros:
                    final.estado = REVER
                    final.notas.append("outros documentos: " + "; ".join(f"{f}={v!r}" for f, v in outros.items()))
            res.campos[c.nome] = final

    # 3. análises
    if cfg.analises:
        rc_cx = res.campos.get(cfg.campo_data_cirurgia)
        data_cx = rc_cx.valor if rc_cx and isinstance(rc_cx.valor, date) else None
        if data_cx is None and por_tipo["cirurgia"]:
            d = data_documento(por_tipo["cirurgia"][0].texto, preferir=("cirurgia", "interven"))
            data_cx = d.valor if d else None
        params = [a.model_dump() for a in cfg.analises]
        valores: list[ValorAnalise] = []
        for doc in por_tipo["analises"]:
            valores.extend(extrair_valores(doc.texto, params, ficheiro=doc.nome))
        for a in cfg.analises:
            vs = [v for v in valores if v.parametro == a.nome]
            cand = escolher_valor(vs, a.regra, data_cx, a.janela_dias)
            rc = ResultadoCampo(campo=a.nome)
            if cand is not None:
                rc.valor = cand.valor
                rc.valor_regex = cand.valor
                rc.evidencia = cand.evidencia
                rc.ficheiro = cand.ficheiro
                rc.fonte = "regex" if cand.valor is not None else ""
                if cand.nota:
                    rc.notas.append(cand.nota)
                duvida = any(k in cand.nota for k in ("confirmar", "reportado", "ignorados"))
                rc.estado = SEM_DADOS if cand.valor is None else (REVER if duvida else OK)
                if len(vs) > 1 and cand.valor is not None:
                    rc.notas.append(f"{len(vs)} valores disponíveis; regra '{a.regra}'")
            res.campos[a.nome] = rc

    # 4. campos derivados e "não aplicável"
    _aplicar_regras(res, cfg)
    return res


def _aplicar_regras(res: ResultadoDoente, cfg: ConfigCampos) -> None:
    valores = lambda: {k: c.valor for k, c in res.campos.items()}  # noqa: E731
    for c in cfg.campos:
        if not c.derivado:
            continue
        rc = res.campos.setdefault(c.nome, ResultadoCampo(campo=c.nome))
        _nome, args = parse_chamada(c.derivado)
        r = calcular(c.derivado, valores())
        if r is None:
            continue
        valor, nota = r
        valor, erro = coagir(c.tipo, valor, c.opcoes)
        if valor is None:
            continue
        if rc.valor is None:
            rc.valor, rc.fonte, rc.ficheiro = valor, "derivado", ""
            rc.evidencia = ", ".join(f"{a}={res.campos[a].valor}" for a in args if a in res.campos)
            rc.notas.append(nota)
            fontes_duvidosas = [a for a in args if a in res.campos and res.campos[a].estado == REVER]
            rc.estado = REVER if ("assume" in nota or fontes_duvidosas or erro) else OK
            if fontes_duvidosas:
                rc.notas.append(f"depende de campos a rever: {', '.join(fontes_duvidosas)}")
        elif not _iguais(rc.valor, valor):
            rc.estado = REVER
            rc.notas.insert(0, f"DISCORDÂNCIA com valor calculado ({valor!r}: {nota})")

    for c in cfg.campos:
        if not c.nao_aplicavel_se:
            continue
        v = valores()
        cond = next((x for x in c.nao_aplicavel_se if avaliar_condicao(x, v)), None)
        if cond is None:
            continue
        rc = res.campos.setdefault(c.nome, ResultadoCampo(campo=c.nome))
        if rc.valor not in (None, NAO_APLICAVEL):
            rc.notas.insert(0, f"valor extraído {rc.valor!r} substituído por 'não aplicável' ({cond})")
            rc.estado = REVER
        else:
            origem = cond.split()[0]
            rc.estado = res.campos[origem].estado if origem in res.campos else OK
        rc.notas = [n for n in rc.notas if n != "LLM desativado"]
        rc.valor, rc.fonte = NAO_APLICAVEL, "regra"
        rc.evidencia = cond


def processar_lote(pasta_entrada: str | Path, cfg: ConfigCampos, llm: ClienteLLM | None = None,
                   usar_ocr: bool = True, progresso: Callable[[str], None] | None = None,
                   correspondencia: dict[str, set[str]] | None = None) -> list[ResultadoDoente]:
    """Cada subpasta de `pasta_entrada` = um doente (nome da pasta = patient_id)."""
    pasta_entrada = Path(pasta_entrada)
    pastas = sorted(p for p in pasta_entrada.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not pastas:
        raise FileNotFoundError(
            f"Sem subpastas em {pasta_entrada}. Estrutura esperada: {pasta_entrada}/<patient_id>/*.pdf")
    return [processar_doente(p, cfg, llm, usar_ocr, progresso, correspondencia) for p in pastas]
