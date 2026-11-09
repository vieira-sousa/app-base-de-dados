"""Exportação para revisão (Excel/JSON) e preenchimento da base de dados Excel."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .derivados import NAO_APLICAVEL
from .esquema import ConfigBaseDados, ConfigCampos, coagir
from .motor import OK, REVER, SEM_DADOS, ResultadoDoente
from .utils import normalizar, normalizar_id

CORES = {OK: "C6EFCE", REVER: "FFEB9C", SEM_DADOS: "EDEDED"}
COLUNAS_REVISAO = ["id_doente", "campo", "valor", "estado", "aprovar", "fonte", "valor_regex",
                   "valor_llm", "evidencia", "ficheiro", "notas"]


def _celula(v: Any) -> Any:
    if isinstance(v, (date, datetime)) or v is None or isinstance(v, (int, float, str)):
        return v
    return str(v)


def _txt_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    return _celula(v)


def guardar_json(resultados: list[ResultadoDoente], pasta: str | Path) -> None:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    for r in resultados:
        (pasta / f"{r.id_doente}.json").write_text(
            json.dumps(r.para_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def exportar_revisao(resultados: list[ResultadoDoente], cfg: ConfigCampos, caminho: str | Path) -> Path:
    """Cria revisao.xlsx: folha 'Revisao' (1 linha por doente×campo), 'Resumo' e 'Avisos'."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # --- Revisão (formato longo, é aqui que se valida)
    ws = wb.active
    ws.title = "Revisao"
    ws.append(COLUNAS_REVISAO)
    for r in resultados:
        for nome in cfg.nomes():
            c = r.campos.get(nome)
            if c is None:
                continue
            ws.append([
                r.id_doente, nome, _txt_bool(c.valor), c.estado,
                "S" if c.estado == OK else "",
                c.fonte, _txt_bool(c.valor_regex), _txt_bool(c.valor_llm),
                c.evidencia, c.ficheiro, " | ".join(c.notas),
            ])
            fill = PatternFill("solid", fgColor=CORES.get(c.estado, "FFFFFF"))
            for col in range(1, 6):
                ws.cell(ws.max_row, col).fill = fill
            if isinstance(c.valor, date):
                ws.cell(ws.max_row, 3).number_format = "DD/MM/YYYY"
    dv = DataValidation(type="list", formula1='"S,N"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"E2:E{max(ws.max_row, 2)}")
    larguras = [12, 24, 22, 10, 9, 10, 18, 18, 70, 28, 60]
    for i, w in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cel in ws[1]:
        cel.font = Font(bold=True)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    # --- Resumo (formato largo, 1 linha por doente)
    wr = wb.create_sheet("Resumo")
    nomes = cfg.nomes()
    wr.append(["id_doente"] + nomes)
    for r in resultados:
        wr.append([r.id_doente] + [_txt_bool(r.campos[n].valor) if n in r.campos else None for n in nomes])
        for j, n in enumerate(nomes, 2):
            if n in r.campos:
                wr.cell(wr.max_row, j).fill = PatternFill("solid", fgColor=CORES.get(r.campos[n].estado, "FFFFFF"))
                if isinstance(r.campos[n].valor, date):
                    wr.cell(wr.max_row, j).number_format = "DD/MM/YYYY"
    for cel in wr[1]:
        cel.font = Font(bold=True)
        cel.alignment = Alignment(text_rotation=45)
    wr.freeze_panes = "B2"

    # --- Avisos e documentos
    wa = wb.create_sheet("Avisos")
    wa.append(["id_doente", "aviso"])
    for r in resultados:
        for a in r.avisos:
            wa.append([r.id_doente, a])
    wa.column_dimensions["B"].width = 140
    wd = wb.create_sheet("Documentos")
    wd.append(["id_doente", "ficheiro", "tipo", "metodo_texto", "caracteres"])
    for r in resultados:
        for d in r.documentos:
            wd.append([r.id_doente, d["ficheiro"], d["tipo"], d["metodo_texto"], d["caracteres"]])

    # --- Instruções
    wi = wb.create_sheet("Instrucoes", 0)
    for linha in [
        "COMO REVER",
        "1. Folha 'Revisao': cada linha é um campo de um doente.",
        "2. Verde = regex e LLM concordam (ou fonte única verificada). Amarelo = REVER. Cinzento = sem dados.",
        "3. Corrija a coluna 'valor' se necessário e escreva S na coluna 'aprovar' para enviar para a BD.",
        "   Linhas sem S não são escritas. Para preencher um campo sem dados, escreva o valor e S.",
        "4. Booleanos: Sim/Não. Datas: DD/MM/AAAA.",
        "5. Depois: python -m extrator preencher <este ficheiro>   (é feito backup da BD antes).",
    ]:
        wi.append([linha])
    wi["A1"].font = Font(bold=True, size=14)
    wi.column_dimensions["A"].width = 120

    wb.save(caminho)
    return caminho


# --------------------------------------------------------- ler revisão
def ler_revisao(caminho: str | Path) -> list[dict]:
    wb = load_workbook(caminho, data_only=True)
    ws = wb["Revisao"]
    linhas = list(ws.iter_rows(values_only=True))
    cab = [str(c).strip() if c is not None else "" for c in linhas[0]]
    idx = {nome: cab.index(nome) for nome in ("id_doente", "campo", "valor", "aprovar")}
    out = []
    for row in linhas[1:]:
        if row is None or all(v is None for v in row):
            continue
        out.append({k: row[i] for k, i in idx.items()})
    return out


# --------------------------------------------------- preencher base de dados
SEM_CODIGO = object()


def _recodificar(valor: Any, campo: str, cfg_bd: ConfigBaseDados) -> Any:
    """Converte o valor extraído para o código da BD. Devolve SEM_CODIGO se não houver código."""
    tabela = cfg_bd.recodificacao.get(campo)
    if valor == NAO_APLICAVEL:
        if tabela and NAO_APLICAVEL in tabela:
            return tabela[NAO_APLICAVEL]
        return cfg_bd.recodificacao.get("_geral", {}).get(NAO_APLICAVEL, SEM_CODIGO)
    if tabela:
        for k, v in tabela.items():
            if isinstance(valor, bool) or isinstance(k, bool):
                if isinstance(valor, bool) and isinstance(k, bool) and k == valor:
                    return v
                continue
            if normalizar(str(k)) == normalizar(str(valor)):
                return v
        return SEM_CODIGO
    if isinstance(valor, bool):
        padrao = cfg_bd.recodificacao.get("_booleano")
        if padrao:
            return padrao.get(valor, int(valor))
        return int(valor)
    return valor


def _vazio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _colunas(ws, linha: int) -> dict[str, int]:
    return {normalizar(str(c.value)): c.column for c in ws[linha] if c.value is not None}


def _encontrar_coluna(nome: str, colunas: dict[str, int]) -> int | None:
    """Correspondência exata (sem maiúsculas/acentos) ou cabeçalho anotado: 'operative_time_min_Difícil'."""
    n = normalizar(nome)
    if n in colunas:
        return colunas[n]
    candidatos = [c for k, c in colunas.items() if k.startswith(n + "_") or k.startswith(n + " ")]
    return candidatos[0] if len(candidatos) == 1 else None


def _listas_validacao(ws) -> dict[int, set[str]]:
    """Valores permitidos por coluna, lidos das validações de dados do Excel (listas "0,1,9")."""
    from openpyxl.utils.cell import range_boundaries

    out: dict[int, set[str]] = {}
    for dv in ws.data_validations.dataValidation:
        if dv.type != "list" or not dv.formula1 or not dv.formula1.startswith('"'):
            continue
        permitidos = {x.strip() for x in dv.formula1.strip('"').split(",")}
        for intervalo in str(dv.sqref).split():
            c1, _r1, c2, _r2 = range_boundaries(intervalo)
            for col in range(c1, c2 + 1):
                out[col] = permitidos
    return out


def _formatar_codigo(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def preencher_base_dados(
    revisao: list[dict],
    cfg_campos: ConfigCampos,
    cfg_bd: ConfigBaseDados,
    caminho_bd: str | Path | None = None,
    simular: bool = False,
    pasta_registo: str | Path = "resultados",
) -> dict:
    """Escreve na BD Excel as linhas aprovadas (aprovar == 'S'). Faz backup antes.

    Devolve um resumo com contagens e o caminho do registo de alterações.
    """
    caminho_bd = Path(caminho_bd or cfg_bd.ficheiro)
    if not caminho_bd.exists():
        raise FileNotFoundError(f"Base de dados não encontrada: {caminho_bd}")

    keep_vba = caminho_bd.suffix.lower() == ".xlsm"
    wb = load_workbook(caminho_bd, keep_vba=keep_vba)
    ws = wb[cfg_bd.folha] if cfg_bd.folha else wb.active

    h = cfg_bd.linha_cabecalho
    colunas = _colunas(ws, h)
    col_id = _encontrar_coluna(cfg_bd.coluna_id, colunas)
    if col_id is None:
        raise KeyError(f"Coluna de ID '{cfg_bd.coluna_id}' não encontrada na linha {h} da folha '{ws.title}'")

    # campo -> coluna
    if cfg_bd.mapeamento == "auto":
        pedidos = {n: n for n in cfg_campos.nomes()}
    else:
        pedidos = dict(cfg_bd.mapeamento)
    mapa_col = {campo: _encontrar_coluna(col, colunas) for campo, col in pedidos.items()}
    faltam = sorted(campo for campo, col in mapa_col.items() if col is None)
    nome_col = {c.column: str(c.value) for c in ws[h] if c.value is not None}
    validacao = _listas_validacao(ws)

    linhas_id: dict[str, int] = {}
    for row in range(h + 1, ws.max_row + 1):
        v = normalizar_id(ws.cell(row, col_id).value)
        if v:
            linhas_id.setdefault(v, row)
    colunas_dados = [c for c in mapa_col.values() if c]

    def linha_livre() -> int:
        """Primeira linha sem ID e sem dados (a BD pode ter linhas pré-numeradas com fórmulas)."""
        usadas = set(linhas_id.values())
        for row in range(h + 1, ws.max_row + 1):
            if row in usadas or not _vazio(ws.cell(row, col_id).value):
                continue
            if all(_vazio(ws.cell(row, c).value) or ws.cell(row, c).data_type == "f" for c in colunas_dados):
                return row
        return ws.max_row + 1

    registo = []
    contagem = {"escritos": 0, "iguais": 0, "conflitos": 0, "nao_aprovados": 0,
                "sem_coluna": 0, "linhas_novas": 0, "invalidos": 0}

    for item in revisao:
        id_d = normalizar_id(item["id_doente"])
        campo = item["campo"]
        if str(item.get("aprovar") or "").strip().upper() != "S":
            contagem["nao_aprovados"] += 1
            continue
        col = mapa_col.get(campo)
        if col is None:
            contagem["sem_coluna"] += 1
            registo.append([id_d, campo, pedidos.get(campo, ""), None, _celula(item["valor"]), "sem coluna na BD"])
            continue
        coluna_nome = nome_col.get(col, "")

        tipo = cfg_campos.tipo_de(campo)
        opcoes = next((c.opcoes for c in cfg_campos.campos if c.nome == campo), None)
        valor, erro = coagir(tipo, item["valor"], opcoes)
        if erro:
            contagem["invalidos"] += 1
            registo.append([id_d, campo, coluna_nome, None, _celula(item["valor"]), f"valor inválido: {erro}"])
            continue
        if valor is None:
            continue
        original = valor
        valor = _recodificar(valor, campo, cfg_bd)
        if valor is SEM_CODIGO:
            contagem["invalidos"] += 1
            registo.append([id_d, campo, coluna_nome, None, _celula(original),
                            "sem código na BD para este valor (ver recodificacao em base_dados.yaml) — preencher à mão"])
            continue
        if col in validacao and _formatar_codigo(valor) not in validacao[col]:
            contagem["invalidos"] += 1
            registo.append([id_d, campo, coluna_nome, None, _celula(valor),
                            f"código fora da lista de validação da BD {sorted(validacao[col])}"])
            continue

        row = linhas_id.get(id_d)
        if row is None:
            if not cfg_bd.criar_linhas_novas:
                registo.append([id_d, campo, coluna_nome, None, _celula(valor), "doente não existe na BD"])
                continue
            row = linha_livre()
            ws.cell(row, col_id).value = id_d
            linhas_id[id_d] = row
            contagem["linhas_novas"] += 1
            registo.append([id_d, "", nome_col.get(col_id, ""), None, id_d, f"doente acrescentado na linha {row}"])

        cel = ws.cell(row, col)
        if cel.data_type == "f":
            contagem["invalidos"] += 1
            registo.append([id_d, campo, coluna_nome, None, _celula(valor), "célula com fórmula — não alterada"])
            continue
        antigo = cel.value
        if not _vazio(antigo):
            a = antigo.date() if isinstance(antigo, datetime) else antigo
            if _formatar_codigo(a).lower() == _formatar_codigo(valor).lower():
                contagem["iguais"] += 1
                continue
            if not cfg_bd.sobrescrever:
                contagem["conflitos"] += 1
                registo.append([id_d, campo, coluna_nome, _celula(antigo), _celula(valor),
                                "CONFLITO: célula já preenchida com valor diferente (não alterada)"])
                continue
        cel.value = valor
        if isinstance(valor, date) and cel.number_format == "General":
            cel.number_format = "DD/MM/YYYY"
        contagem["escritos"] += 1
        registo.append([id_d, campo, coluna_nome, _celula(antigo), _celula(valor),
                        "sobrescrito" if not _vazio(antigo) else "escrito"])

    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_registo = Path(pasta_registo)
    pasta_registo.mkdir(parents=True, exist_ok=True)
    caminho_registo = pasta_registo / f"registo_preenchimento_{carimbo}{'_SIMULACAO' if simular else ''}.xlsx"
    wl = Workbook()
    wsl = wl.active
    wsl.append(["id_doente", "campo", "coluna_bd", "valor_antigo", "valor_novo", "acao"])
    for linha in registo:
        wsl.append(linha)
    wsl.column_dimensions["F"].width = 90
    wl.save(caminho_registo)

    backup = None
    if not simular:
        backup = caminho_bd.with_name(f"{caminho_bd.stem}.backup_{carimbo}{caminho_bd.suffix}")
        shutil.copy2(caminho_bd, backup)
        wb.save(caminho_bd)

    return {"contagem": contagem, "registo": str(caminho_registo),
            "backup": str(backup) if backup else None, "campos_sem_coluna": faltam}


def ler_revisao_completa(caminho: str | Path) -> list[dict]:
    """Todas as colunas da folha Revisao (para a interface gráfica)."""
    ws = load_workbook(caminho, data_only=True)["Revisao"]
    linhas = list(ws.iter_rows(values_only=True))
    cab = [str(c) if c is not None else "" for c in linhas[0]]
    return [dict(zip(cab, row)) for row in linhas[1:] if row and any(v is not None for v in row)]


def guardar_revisao_editada(caminho: str | Path, linhas: list[dict]) -> None:
    """Grava as colunas 'valor' e 'aprovar' editadas na interface (mesma ordem de linhas)."""
    wb = load_workbook(caminho)
    ws = wb["Revisao"]
    cab = [c.value for c in ws[1]]
    iv, ia = cab.index("valor") + 1, cab.index("aprovar") + 1
    ic, ii = cab.index("campo") + 1, cab.index("id_doente") + 1
    for i, linha in enumerate(linhas, start=2):
        if str(ws.cell(i, ic).value) != str(linha["campo"]) or str(ws.cell(i, ii).value) != str(linha["id_doente"]):
            raise ValueError(f"Linha {i} não corresponde ({linha['id_doente']}/{linha['campo']}). Ficheiro alterado?")
        ws.cell(i, iv).value = _celula(linha["valor"])
        ws.cell(i, ia).value = linha.get("aprovar") or None
    wb.save(caminho)
