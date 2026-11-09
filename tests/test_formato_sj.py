"""Formatos dos documentos do hospital (AP sinóptica, SClínico, laboratório Maxdata).

Os ficheiros em fixtures/formato_sj são FICTÍCIOS: reproduzem só a estrutura e o vocabulário.
"""
from datetime import date
from pathlib import Path

import pytest

from extrator.derivados import NAO_APLICAVEL
from extrator.esquema import RAIZ, carregar_campos
from extrator.motor import OK, REVER, processar_doente

FIX = Path(__file__).parent / "fixtures" / "formato_sj"


@pytest.fixture(scope="module")
def cfg():
    return carregar_campos(RAIZ / "config" / "campos.yaml")


def valores(r):
    return {k: c.valor for k, c in r.campos.items()}


def test_sj001_carcinoma_laparoscopia_assistida(cfg):
    r = processar_doente(FIX / "SJ001", cfg)
    v = valores(r)
    tipos = {d["ficheiro"]: d["tipo"] for d in r.documentos}
    assert tipos == {"AP.txt": "anatomia_patologica", "relato.txt": "cirurgia", "EA.txt": "analises"}
    assert not any("DIFERENTES" in a for a in r.avisos)       # mesmo nº de processo nos 3 formatos
    assert not any("elegível" in a for a in r.avisos)

    # relatório operatório (SClínico)
    assert v["date_surgery"] == date(2024, 3, 8)
    assert v["emergency_surgery"] is False                     # "PROD. PROGRAMADA BASE"
    assert v["approach"] == "laparoscopica" and v["conversion"] is False
    assert v["anastomosis"] is True
    assert v["intracorporeal_anastomosis"] is False            # assistida + exteriorização
    assert v["ascites_intraop"] is False and v["peritoneal_implants_intraop"] is False   # "peritoneias"
    assert v["blood_loss_ml"] == 100

    # anatomia patológica (formato sinóptico)
    assert v["tumor_location"] == "cego"
    assert v["tumor_size_mm"] == 52.0
    assert v["histology"] == "adenocarcinoma_nos" and v["grade"] == "G3"
    assert v["t_stage"] == "T4a" and v["n_stage"] == "N2a" and v["tnm_prefix"] == "p"
    assert v["nodes_examined"] == 31 and v["nodes_positive"] == 4
    assert v["lymphatic_invasion"] is True                     # "Permeação linfática: observada"
    assert v["venous_invasion"] is True
    assert v["perineural_invasion"] is False                   # "invasão peri-neural: não observada"
    assert v["tumor_deposits"] is True and v["n_tumor_deposits"] == 3
    assert v["tumor_budding"] == "alto"
    assert v["pathological_r_status"] == "R0" and v["positive_margin"] is False
    assert (v["mlh1"], v["pms2"], v["msh2"], v["msh6"]) == ("perdido", "perdido", "retido", "retido")
    assert v["mmr_status"] == "dMMR"
    assert v["pathological_stage"] == "III"

    # laboratório: "% e absoluto" na mesma linha, unidades "x 10⁹/L"
    assert v["hb_preop"] == 11.2
    assert v["wbc_preop"] == 8.1 and r.campos["wbc_preop"].estado == OK
    assert v["neutrophils_preop"] == 5.7 and v["lymphocytes_preop"] == 1.5
    assert v["platelets_preop"] == 388
    assert v["albumin_preop"] == 35.0 and v["crp_preop"] == 14.2
    assert v["cea_preop"] == 9.8 and v["ca199_preop"] == 22


def test_sj002_adenoma_convertida_urgente(cfg):
    r = processar_doente(FIX / "SJ002", cfg)
    v = valores(r)
    assert any("elegível" in a for a in r.avisos)               # AP sem carcinoma
    assert v["emergency_surgery"] is True
    assert v["approach"] == "laparoscopica" and v["conversion"] is True   # "conversão para lapatorotomia"
    assert v["intracorporeal_anastomosis"] == NAO_APLICAVEL     # convertida
    assert v["peritoneal_implants_intraop"] is False            # "carciomatose"
    assert v["ascites_intraop"] is False                        # "sem líquido livre"
    assert v["obstruction"] is True
    assert v["blood_loss_ml"] == 0 and r.campos["blood_loss_ml"].estado == REVER
    assert v["tumor_location"] == "cego"
    assert v["tumor_size_mm"] == 20.0                           # "2,0(longitudinal)x1,5(transversal)…"
    assert v["nodes_examined"] == 19 and v["nodes_positive"] == 0   # "(n=19) sem alterações"
    assert v["lymphatic_invasion"] is False and v["venous_invasion"] is False
    assert v["perineural_invasion"] is False                    # "permeação perineural"
    assert v["pathological_r_status"] == "R0"                   # "Topos ... sem sinais de envolvimento"
    assert v["histology"] is None and v["grade"] is None        # displasia de alto grau ≠ grau


def test_dois_tumores_e_itc():
    """AP com adenocarcinoma + tumor neuroendócrino: dados do adenocarcinoma, tudo para revisão."""
    from extrator.regras import REGISTO
    from extrator.regras.anatomia_patologica import varios_tumores

    texto = (
        "Classificação da OMS: Adenocarcinoma, SOE; moderadamente diferenciado;\n"
        "Nos gânglios linfáticos observa-se:\n"
        " - 1 / 40 gânglio linfático com células tumorais isoladas de adenocarcinoma - pN0(i+);\n"
        " - 2 / 40 gânglios linfáticos com metástase de tumor neuroendócrino.\n"
        "CONCLUSÃO:\n. Adenocarcinoma SOE, moderadamente diferenciado, do colon ascendente\n"
        "   - pT3 N0(i+) R0 (UICC/AJCC 8.ª edição)\n"
        ". Tumor neuroendócrino bem diferenciado do íleon, G1\n   - pT2 N1 R0\n"
    )
    assert varios_tumores(texto)
    assert REGISTO["colon.histologia"](texto).valor == "adenocarcinoma_nos"
    assert REGISTO["ap.grau_diferenciacao"](texto).valor == "G2"       # não o G1 do neuroendócrino
    assert REGISTO["ap.pt"](texto).valor == "T3"
    assert REGISTO["ap.ganglios_positivos"](texto).valor == 0          # ITC não conta
    assert REGISTO["ap.ganglios_examinados"](texto).valor == 40
