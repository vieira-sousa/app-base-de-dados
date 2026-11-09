"""Estudo hemicolectomia direita: regex de cólon, derivados, recodificação e BD com linhas pré-numeradas."""
import shutil
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

from extrator.derivados import NAO_APLICAVEL, estadio_ajcc
from extrator.esquema import RAIZ, ConfigBaseDados, carregar_campos
from extrator.exportar import exportar_revisao, ler_revisao, preencher_base_dados
from extrator.motor import OK, REVER, processar_doente
from extrator.regras import REGISTO

COLON = Path(__file__).parent / "fixtures" / "colon"


def ext(nome, texto):
    c = REGISTO[nome](texto)
    return None if c is None else c.valor


# ------------------------------------------------------------------ regex AP
@pytest.mark.parametrize("texto,esperado", [
    ("DIAGNÓSTICO: Adenocarcinoma do cego, pT3", "adenocarcinoma_nos"),
    ("Adenocarcinoma mucinoso do cólon ascendente", "mucinoso"),
    ("Carcinoma de células em anel de sinete", "anel_sinete"),
    ("Carcinoma medular do cego", "medular"),
    ("Adenocarcinoma com componente mucinoso (30%)", "adenocarcinoma_nos"),
])
def test_histologia(texto, esperado):
    assert ext("colon.histologia", texto) == esperado


@pytest.mark.parametrize("texto,muc,pct", [
    ("Adenocarcinoma com componente mucinoso (30%).", True, 30.0),
    ("Componente mucinoso em cerca de 40% do tumor.", True, 40.0),
    ("Sem componente mucinoso.", False, None),
    ("Adenocarcinoma moderadamente diferenciado.", None, None),
])
def test_mucinoso(texto, muc, pct):
    assert ext("colon.componente_mucinoso", texto) == muc
    assert ext("colon.percentagem_mucinosa", texto) == pct


@pytest.mark.parametrize("texto,esperado", [
    ("Não se observam células em anel de sinete.", False),
    ("Presença de células em anel de sinete (<50%).", True),
])
def test_anel_sinete(texto, esperado):
    assert ext("colon.componente_anel_sinete", texto) == esperado


@pytest.mark.parametrize("texto,pres,n", [
    ("Depósitos tumorais: presentes (2).", True, 2),
    ("Identificaram-se 3 depósitos tumorais.", True, 3),
    ("Depósitos tumorais: ausentes.", False, None),
    ("Sem depósitos tumorais.", False, None),
    ("pT3 pN1c", True, None),
])
def test_depositos(texto, pres, n):
    assert ext("colon.depositos_tumorais", texto) == pres
    assert ext("colon.n_depositos_tumorais", texto) == n


@pytest.mark.parametrize("texto,esperado", [
    ("Budding tumoral: baixo (Bd1)", "baixo"),
    ("Tumor budding: Bd3", "alto"),
    ("budding intermédio", "intermedio"),
])
def test_budding(texto, esperado):
    assert ext("colon.budding", texto) == esperado


@pytest.mark.parametrize("texto,mlh1,pms2,msh2,msh6", [
    ("Expressão nuclear mantida de MLH1, PMS2, MSH2 e MSH6.", "retido", "retido", "retido", "retido"),
    ("Perda de expressão de MLH1 e PMS2, com expressão mantida de MSH2 e MSH6.", "perdido", "perdido", "retido", "retido"),
    ("MLH1: positivo\nPMS2: positivo\nMSH2: negativo\nMSH6: negativo", "retido", "retido", "perdido", "perdido"),
])
def test_proteinas_mmr(texto, mlh1, pms2, msh2, msh6):
    assert [ext(f"colon.{p}", texto) for p in ("mlh1", "pms2", "msh2", "msh6")] == [mlh1, pms2, msh2, msh6]


@pytest.mark.parametrize("texto,esperado", [
    ("Estado MMR: pMMR", "pMMR"),
    ("Proteínas de reparação: deficiente", "dMMR"),
    ("Sem perda de expressão das proteínas de reparação.", "pMMR"),
])
def test_mmr(texto, esperado):
    assert ext("colon.mmr", texto) == esperado


@pytest.mark.parametrize("texto,kras,braf", [
    ("KRAS: mutado (G12D). BRAF: wild-type.", "mutado", "WT"),
    ("Não foram detetadas mutações no KRAS. BRAF V600E: não detetada.", "WT", "WT"),
    ("Pesquisa de mutações KRAS: negativa; BRAF: mutação V600E detetada", "WT", "mutado"),
])
def test_genes(texto, kras, braf):
    assert ext("colon.kras", texto) == kras
    assert ext("colon.braf", texto) == braf


@pytest.mark.parametrize("texto,esperado", [
    ("MSS (microssatélites estáveis)", "MSS/MSI-L"),
    ("Tumor MSI-H", "MSI-H"),
])
def test_msi(texto, esperado):
    assert ext("colon.msi", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Adenocarcinoma do cólon ascendente", "ascendente"),
    ("Topografia: cego", "cego"),
    ("Neoplasia localizada no ângulo hepático", "angulo_hepatico"),
    # menção anatómica da peça, sem tumor: não deve contar
    ("Peça incluindo íleo terminal, cego, apêndice e cólon ascendente.", None),
])
def test_localizacao(texto, esperado):
    assert ext("colon.localizacao", texto) == esperado


# ------------------------------------------------------------- regex cirurgia
@pytest.mark.parametrize("texto,esperado", [
    ("Hemicolectomia direita laparoscópica", "hemicolectomia_direita"),
    ("Hemicolectomia direita alargada ao transverso", "direita_alargada"),
    ("Colectomia subtotal", "outro"),
])
def test_tipo_colectomia(texto, esperado):
    assert ext("colon.tipo_colectomia", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Tipo de cirurgia: Programada", False),
    ("Cirurgia urgente por oclusão intestinal", True),
    ("Doente admitido pelo Serviço de Urgência", True),
])
def test_urgente(texto, esperado):
    assert ext("colon.urgente", texto) == esperado


@pytest.mark.parametrize("texto,abordagem,conversao", [
    ("Hemicolectomia direita laparoscópica. Sem necessidade de conversão.", "laparoscopica", False),
    ("Iniciada por laparoscopia, com conversão para laparotomia.", "laparoscopica", True),
    ("Laparotomia mediana.", "aberta", None),
    ("Abordagem robótica (da Vinci Xi).", "robotica", False),
])
def test_abordagem_inicial(texto, abordagem, conversao):
    assert ext("cirurgia.abordagem_inicial", texto) == abordagem
    assert ext("cirurgia.conversao", texto) == conversao


@pytest.mark.parametrize("texto,anast,intra", [
    ("Anastomose ileocólica latero-lateral intracorpórea mecânica.", True, True),
    ("Exteriorização da peça e anastomose ileotransversa extracorpórea manual.", True, False),
    ("Ileostomia terminal, sem anastomose.", False, None),
])
def test_anastomose(texto, anast, intra):
    assert ext("colon.anastomose_realizada", texto) == anast
    assert ext("colon.anastomose_intracorporea", texto) == intra


@pytest.mark.parametrize("texto,ascite,implantes", [
    ("Sem ascite, sem carcinomatose peritoneal.", False, False),
    ("Ascite em pequena quantidade. Implantes peritoneais no grande epíploon.", True, True),
    ("Inventário: sem ascite ou implantes peritoneais.", False, False),
])
def test_achados_peritoneais(texto, ascite, implantes):
    assert ext("colon.ascite", texto) == ascite
    assert ext("colon.implantes_peritoneais", texto) == implantes


def test_asa():
    assert ext("colon.asa", "ASA: III") == "III"
    assert ext("colon.asa", "ASA 2") == "II"


# ------------------------------------------------------------------ derivados
@pytest.mark.parametrize("t,n,m,esperado", [
    ("T2", "N0", None, "I"), ("T4a", "N0", None, "II"), ("T1", "N1c", None, "III"),
    ("T3", "N0", "M1a", "IV"), ("Tis", "N0", None, None),
])
def test_estadio(t, n, m, esperado):
    r = estadio_ajcc(t, n, m)
    assert (r[0] if r else None) == esperado


# ------------------------------------------------------- doente completo
@pytest.fixture
def cfg():
    return carregar_campos(RAIZ / "config" / "campos.yaml")


@pytest.fixture
def resultado(tmp_path, cfg):
    d = tmp_path / "entrada" / "HCD001"
    d.mkdir(parents=True)
    for f in COLON.glob("*.txt"):
        shutil.copy(f, d / f.name)
    return processar_doente(d, cfg)


def test_doente_colon(resultado):
    v = {k: c.valor for k, c in resultado.campos.items()}
    esperado = {
        "date_surgery": datetime(2025, 5, 12).date(), "operation_type": "hemicolectomia_direita",
        "approach": "laparoscopica", "conversion": False, "intracorporeal_anastomosis": True,
        "emergency_surgery": False, "asa": "II", "operative_time_min": 165, "blood_loss_ml": 50,
        "tumor_location": "ascendente", "tumor_size_mm": 42.0, "histology": "adenocarcinoma_nos",
        "mucinous_component": True, "mucinous_percent": 20.0, "signet_ring_component": False,
        "grade": "G2", "tnm_prefix": "p", "t_stage": "T3", "n_stage": "N1b",
        "nodes_examined": 24, "nodes_positive": 3, "tumor_deposits": True, "n_tumor_deposits": 2,
        "lymphatic_invasion": True, "venous_invasion": False, "perineural_invasion": True,
        "tumor_budding": "baixo", "pathological_r_status": "R0", "positive_margin": False,
        "tumor_regression_grade": NAO_APLICAVEL, "pathological_stage": "III", "mmr_status": "pMMR",
        "hb_preop": 10.9, "neutrophils_preop": 5.32, "lymphocytes_preop": 1.64,
        "crp_preop": 12.0, "albumin_preop": 36.0, "cea_preop": 12.4,
    }
    for k, e in esperado.items():
        assert v[k] == e, k
    assert resultado.campos["pathological_stage"].estado == REVER   # assume M0
    assert resultado.campos["crp_preop"].estado == OK                # conversão não obriga a rever


def test_nao_aplicavel_cirurgia_aberta(tmp_path, cfg):
    d = tmp_path / "HCD002"
    d.mkdir()
    (d / "cirurgia.txt").write_text(
        "RELATÓRIO OPERATÓRIO\nData da cirurgia: 01/02/2025\nLaparotomia mediana. Hemicolectomia direita.\n"
        "Anastomose ileotransversa manual.", encoding="utf-8")
    r = processar_doente(d, cfg)
    assert r.campos["approach"].valor == "aberta"
    assert r.campos["conversion"].valor == NAO_APLICAVEL
    assert r.campos["intracorporeal_anastomosis"].valor == NAO_APLICAVEL


# ----------------------------------------------- BD com a estrutura do estudo
def _bd_estudo(caminho: Path):
    """Cópia mínima da estrutura: cabeçalhos anotados, linhas pré-numeradas com fórmulas, validações."""
    wb = Workbook()
    ws = wb.active
    ws.title = "DOENTES"
    cab = ["N", "patient_id", "date_surgery", "approach", "conversion", "operative_time_min_Difícil",
           "t_stage", "n_stage", "grade", "tumor_location_DIFÍCIL", "nodes_examined", "nodes_ge12",
           "tumor_regression_grade", "crp_preop"]
    ws.append(cab)
    for i in range(1, 21):
        ws.append([i, None, None, None, None, None, None, None, None, None, None,
                   f'=IF(K{i + 1}="","",IF(K{i + 1}>=12,1,0))'])
    ws.cell(2, 2).value = "HCD000"          # doente já existente
    dv = DataValidation(type="list", formula1='"0,1,8,9"')
    ws.add_data_validation(dv)
    dv.add("E2:E21")
    wb.save(caminho)


def test_preencher_bd_estudo(resultado, cfg, tmp_path):
    rev = exportar_revisao([resultado], cfg, tmp_path / "revisao.xlsx")
    linhas = ler_revisao(rev)
    for linha in linhas:
        if linha["valor"] not in (None, ""):
            linha["aprovar"] = "S"
    linhas.append({"id_doente": "HCD001", "campo": "grade", "valor": "baixo_grau", "aprovar": "S"})

    bd = tmp_path / "bd.xlsx"
    _bd_estudo(bd)
    y = yaml.safe_load((RAIZ / "config" / "base_dados.exemplo.yaml").read_text(encoding="utf-8"))
    y["ficheiro"] = str(bd)
    r = preencher_base_dados(linhas, cfg, ConfigBaseDados(**y), pasta_registo=tmp_path)

    ws = load_workbook(bd)["DOENTES"]
    cab = [c.value for c in ws[1]]
    linha = {cab[c - 1]: ws.cell(3, c).value for c in range(1, ws.max_column + 1)}
    assert ws.max_row == 21                                  # usou linha pré-numerada, não acrescentou
    assert linha["N"] == 2 and linha["patient_id"] == "HCD001"
    assert linha["approach"] == 2 and linha["conversion"] == 0
    assert linha["operative_time_min_Difícil"] == 165        # cabeçalho anotado
    assert linha["t_stage"] == 3 and linha["n_stage"] == 2
    assert linha["tumor_location_DIFÍCIL"] == 2
    assert linha["tumor_regression_grade"] == 0              # não aplicável = 0 neste campo
    assert linha["crp_preop"] == 12
    assert str(linha["nodes_ge12"]).startswith("=IF(")       # fórmula intacta
    assert ws.cell(2, 2).value == "HCD000"                   # outro doente intacto
    reg = [row for row in load_workbook(r["registo"]).active.iter_rows(values_only=True)]
    assert any(row[1] == "grade" and "sem código" in row[5] for row in reg)   # baixo_grau não escrito
    assert linha["grade"] == 2                                                # G2 escrito antes


def test_codigo_fora_da_validacao(cfg, tmp_path):
    bd = tmp_path / "bd.xlsx"
    _bd_estudo(bd)
    cfg_bd = ConfigBaseDados(ficheiro=str(bd), folha="DOENTES", coluna_id="patient_id",
                             recodificacao={"_booleano": {True: 5, False: 0}})
    r = preencher_base_dados([{"id_doente": "HCD000", "campo": "conversion", "valor": "Sim", "aprovar": "S"}],
                             cfg, cfg_bd, pasta_registo=tmp_path)
    assert r["contagem"]["invalidos"] == 1 and r["contagem"]["escritos"] == 0
