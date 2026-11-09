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
def _recodificar(valor: Any, campo: str, cfg_bd: ConfigBaseDados) -> Any:
    tabela = cfg_bd.recodificacao.get(campo)
    if tabela:
        if valor in tabela:
            return tabela[valor]
        norm = {normalizar(str(k)): v for k, v in tabela.items()}
        chave = normalizar(str(valor))
        if chave in norm:
            return norm[chave]
    if isinstance(valor, bool):
        padrao = cfg_bd.recodificacao.get("_booleano")
        if padrao:
            return padrao.get(valor, padrao.get(str(valor).lower(), int(valor)))
        return int(valor)
    return valor


def _vazio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


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

    # cabeçalhos
    h = cfg_bd.linha_cabecalho
    colunas: dict[str, int] = {}
    for cel in ws[h]:
        if cel.value is not None:
            colunas[normalizar(str(cel.value))] = cel.column
    col_id = colunas.get(normalizar(cfg_bd.coluna_id))
    if col_id is None:
        raise KeyError(f"Coluna de ID '{cfg_bd.coluna_id}' não encontrada na linha {h} da folha '{ws.title}'")

    faltam = [c for c in cfg_bd.mapeamento.values() if normalizar(c) not in colunas]
    linhas_id: dict[str, int] = {}
    for row in range(h + 1, ws.max_row + 1):
        v = normalizar_id(ws.cell(row, col_id).value)
        if v:
            linhas_id.setdefault(v, row)

    registo = []
    contagem = {"escritos": 0, "iguais": 0, "conflitos": 0, "nao_aprovados": 0,
                "sem_mapeamento": 0, "linhas_novas": 0, "invalidos": 0}

    for item in revisao:
        id_d = normalizar_id(item["id_doente"])
        campo = item["campo"]
        aprovado = str(item.get("aprovar") or "").strip().upper() == "S"
        if not aprovado:
            contagem["nao_aprovados"] += 1
            continue
        coluna_nome = cfg_bd.mapeamento.get(campo)
        if not coluna_nome or normalizar(coluna_nome) not in colunas:
            contagem["sem_mapeamento"] += 1
            registo.append([id_d, campo, coluna_nome or "", None, item["valor"], "sem coluna mapeada"])
            continue
        col = colunas[normalizar(coluna_nome)]

        tipo = cfg_campos.tipo_de(campo)
        opcoes = next((c.opcoes for c in cfg_campos.campos if c.nome == campo), None)
        valor, erro = coagir(tipo, item["valor"], opcoes)
        if erro:
            contagem["invalidos"] += 1
            registo.append([id_d, campo, coluna_nome, None, item["valor"], f"valor inválido: {erro}"])
            continue
        if valor is None:
            continue
        valor = _recodificar(valor, campo, cfg_bd)

        row = linhas_id.get(id_d)
        if row is None:
            if not cfg_bd.criar_linhas_novas:
                registo.append([id_d, campo, coluna_nome, None, valor, "doente não existe na BD"])
                continue
            row = ws.max_row + 1
            ws.cell(row, col_id).value = int(id_d) if id_d.isdigit() and not id_d.startswith("0") else id_d
            linhas_id[id_d] = row
            contagem["linhas_novas"] += 1
            registo.append([id_d, "", cfg_bd.coluna_id, None, id_d, "linha nova criada"])

        cel = ws.cell(row, col)
        antigo = cel.value
        if not _vazio(antigo):
            a = antigo.date() if isinstance(antigo, datetime) else antigo
            if str(a).strip().lower() == str(valor).strip().lower():
                contagem["iguais"] += 1
                continue
            if not cfg_bd.sobrescrever:
                contagem["conflitos"] += 1
                registo.append([id_d, campo, coluna_nome, _celula(antigo), _celula(valor),
                                "CONFLITO: célula já preenchida com valor diferente (não alterada)"])
                continue
        cel.value = valor
        if isinstance(valor, date):
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
    wl.save(caminho_registo)

    backup = None
    if not simular:
        backup = caminho_bd.with_name(f"{caminho_bd.stem}.backup_{carimbo}{caminho_bd.suffix}")
        shutil.copy2(caminho_bd, backup)
        wb.save(caminho_bd)

    return {"contagem": contagem, "registo": str(caminho_registo),
            "backup": str(backup) if backup else None, "colunas_em_falta": faltam}


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
