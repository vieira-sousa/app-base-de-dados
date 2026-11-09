from datetime import date

import pytest

from extrator.regras import REGISTO
from extrator.utils import parse_data, parse_numero


def ext(nome, texto):
    c = REGISTO[nome](texto)
    return None if c is None else c.valor


# ------------------------------------------------------------- utilitários
@pytest.mark.parametrize("txt,esperado", [
    ("15/03/2025", date(2025, 3, 15)),
    ("15-03-25", date(2025, 3, 15)),
    ("2025-03-15", date(2025, 3, 15)),
    ("15 de março de 2025", date(2025, 3, 15)),
    ("31/02/2025", None),
])
def test_parse_data(txt, esperado):
    assert parse_data(txt) == esperado


@pytest.mark.parametrize("txt,esperado", [("3,2", 3.2), ("1.234,5", 1234.5), ("<0,5", 0.5), ("12", 12.0)])
def test_parse_numero(txt, esperado):
    assert parse_numero(txt) == esperado


# ------------------------------------------------------------------- pTNM
@pytest.mark.parametrize("texto,t,n,m,pref", [
    ("Estadiamento: pT3N1aM0", "T3", "N1a", None, None),
    ("ypT2 ypN0 (0/14)", "T2", "N0", None, "y"),
    ("pT4b pN2b pM1a", "T4b", "N2b", "M1a", None),
    ("TNM: pTis pN0", "Tis", "N0", None, None),
    ("estádio pT1 N0 (0/12)", "T1", "N0", None, None),
    ("rpT3 pN1", "T3", "N1", None, "r"),
])
def test_ptnm(texto, t, n, m, pref):
    assert ext("ap.pt", texto) == t
    assert ext("ap.pn", texto) == n
    assert ext("ap.pm", texto) == m
    assert ext("ap.ptnm_prefixo", texto) == pref


def test_ptnm_ignora_estadiamento_clinico():
    assert ext("ap.pt", "Estadiamento clínico cT3N1M0") is None


# --------------------------------------------------------------- gânglios
@pytest.mark.parametrize("texto,pos,tot", [
    ("pN1b (3/22)", 3, 22),
    ("Metástases em 2 de 18 gânglios linfáticos.", 2, 18),
    ("Isolaram-se 15 gânglios linfáticos, 1 com metástase.", 1, 15),
    ("Isolaram-se 15 gânglios linfáticos, nenhum com metástase.", 0, 15),
    ("Identificados 21 gânglios linfáticos, todos sem metástases.", 0, 21),
])
def test_ganglios(texto, pos, tot):
    assert ext("ap.ganglios_positivos", texto) == pos
    assert ext("ap.ganglios_examinados", texto) == tot


def test_ganglios_nao_confunde_datas():
    assert ext("ap.ganglios_examinados", "Relatório de 12/03/2025. Sem gânglios.") is None


# ---------------------------------------------------------------- margens
@pytest.mark.parametrize("texto,esperado", [
    ("Ressecção R0.", "R0"),
    ("Classificação R: R1 (margem circunferencial < 1 mm)", "R1"),
    ("ypT3 ypN0 L0 V0 Pn0 R0", "R0"),
    ("Margens cirúrgicas livres de neoplasia.", "R0"),
    ("Margem distal envolvida por neoplasia.", "R1"),
    ("Margens proximal e distal livres.", "R0"),
])
def test_margem_r(texto, esperado):
    assert ext("ap.margem_r", texto) == esperado


def test_margem_circunferencial():
    assert ext("ap.margem_circunferencial_mm", "Margem circunferencial a 0,8 cm do tumor") == 8.0
    assert ext("ap.margem_circunferencial_mm", "margem radial: 2 mm") == 2.0


# ------------------------------------------------------------------ tumor
def test_tamanho_tumor():
    assert ext("ap.tamanho_tumor_cm", "Tumor ulcerado com 4,5 x 3 x 1 cm.") == 4.5
    assert ext("ap.tamanho_tumor_cm", "Lesão com maior dimensão de 32 mm") == 3.2
    # distância à margem não é tamanho
    assert ext("ap.tamanho_tumor_cm", "Neoplasia situada a 5 cm da margem distal.") is None


@pytest.mark.parametrize("texto,esperado", [
    ("Adenocarcinoma moderadamente diferenciado", "G2"),
    ("Adenocarcinoma bem diferenciado", "G1"),
    ("Grau histológico: G3", "G3"),
    ("Adenocarcinoma de baixo grau", "baixo_grau"),
    ("Adenoma com displasia de alto grau", None),
])
def test_grau(texto, esperado):
    assert ext("ap.grau_diferenciacao", texto) == esperado


@pytest.mark.parametrize("texto,lv,pn", [
    ("Invasão linfovascular: presente. Invasão perineural: ausente.", True, False),
    ("Sem invasão linfovascular. Observa-se invasão perineural.", False, True),
    ("L1 V0 Pn1", True, True),
    ("Não se identifica invasão linfovascular nem perineural.", False, None),
    ("Invasão perineural não identificada.", None, False),
])
def test_invasoes(texto, lv, pn):
    assert ext("ap.invasao_linfovascular", texto) == lv
    assert ext("ap.invasao_perineural", texto) == pn


def test_trg():
    assert ext("ap.grau_regressao", "Grau de regressão tumoral (Mandard): TRG 3") == "Mandard 3"
    assert ext("ap.grau_regressao", "Dworak 2") == "Dworak 2"
    assert ext("ap.grau_regressao", "TNM AJCC 8ª edição") is None


# --------------------------------------------------------------- cirurgia
@pytest.mark.parametrize("texto,esperado", [
    ("Hemicolectomia direita laparoscópica.", "laparoscopica"),
    ("Abordagem robótica (da Vinci Xi).", "robotica"),
    ("Laparotomia mediana supra e infra-umbilical.", "aberta"),
    ("Iniciada por laparoscopia, com conversão para laparotomia por aderências.", "convertida"),
    ("Via laparoscópica. Extração da peça por mini-laparotomia.", "laparoscopica"),
])
def test_abordagem(texto, esperado):
    assert ext("cirurgia.abordagem", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Perdas hemáticas estimadas de 150 mL.", 150),
    ("Perdas hemáticas: 1,2 L", 1200),
    ("Perdas hemáticas mínimas.", 0),
    ("perdas sanguíneas de cerca de 300 cc", 300),
])
def test_perdas(texto, esperado):
    assert ext("cirurgia.perdas_hematicas_ml", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Duração: 3h30", 210),
    ("Duração da cirurgia: 185 minutos", 185),
    ("Início: 09:10  Fim: 13:25", 255),
    ("Hora de início 23:30, hora de fim 01:15", 105),
])
def test_duracao(texto, esperado):
    assert ext("cirurgia.duracao_min", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Ileostomia em ansa de proteção.", "ileostomia"),
    ("Operação de Hartmann com colostomia terminal.", "colostomia"),
    ("Não se realizou estoma de proteção.", "nenhum"),
    ("Encerramento de ileostomia em ansa.", None),
])
def test_estoma(texto, esperado):
    assert ext("cirurgia.estoma", texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Anastomose ileocólica latero-lateral mecânica.", "mecanica"),
    ("Anastomose gastrojejunal manual em dois planos.", "manual"),
    ("Anastomose ileocólica com agrafador linear e encerramento manual do orifício.", "mista"),
])
def test_anastomose(texto, esperado):
    assert ext("cirurgia.anastomose_tecnica", texto) == esperado


def test_data_cirurgia():
    assert ext("cirurgia.data_cirurgia", "Data da cirurgia: 15/03/2025") == date(2025, 3, 15)
    assert ext("cirurgia.data_cirurgia", "Intervenção realizada em 2025-03-15") == date(2025, 3, 15)


def test_processo():
    assert ext("comum.processo", "Nº Processo: 1234567") == "1234567"
    assert ext("comum.processo", "N.º Proc. 0012345") == "0012345"
