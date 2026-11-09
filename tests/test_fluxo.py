"""Testes de ponta a ponta: PDFs -> extração -> revisão -> base de dados Excel."""
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from extrator.esquema import ConfigBaseDados, ConfigLLM, carregar_campos
from extrator.exportar import exportar_revisao, ler_revisao, preencher_base_dados
from extrator.llm import ClienteLLM, construir_schema, verificar_citacao
from extrator.motor import OK, REVER, processar_doente

FIX = Path(__file__).parent / "fixtures"


def txt_para_pdf(origem: Path, destino: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    fonte = "Helvetica"
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        if Path(f).exists():
            pdfmetrics.registerFont(TTFont("DejaVu", f))
            fonte = "DejaVu"
    c = canvas.Canvas(str(destino), pagesize=A4)
    c.setFont(fonte, 9)
    y = 800
    for linha in origem.read_text(encoding="utf-8").splitlines():
        c.drawString(40, y, linha)
        y -= 13
    c.save()


@pytest.fixture
def pasta_doente(tmp_path):
    d = tmp_path / "entrada" / "1234567"
    d.mkdir(parents=True)
    for f in FIX.glob("*.txt"):  # relatórios de reto (configuração genérica)
        txt_para_pdf(f, d / (f.stem + ".pdf"))
    return d


@pytest.fixture
def cfg():
    return carregar_campos(FIX / "campos_generico.yaml")


def test_extracao_so_regex(pasta_doente, cfg):
    r = processar_doente(pasta_doente, cfg, llm=None)
    v = {k: c.valor for k, c in r.campos.items()}
    assert v["data_cirurgia"] == date(2025, 3, 15)
    assert v["abordagem"] == "laparoscopica"
    assert v["perdas_hematicas_ml"] == 150
    assert v["duracao_cirurgia_min"] == 255
    assert v["estoma"] == "ileostomia"
    assert v["pT"] == "T3" and v["pN"] == "N1b" and v["ptnm_prefixo"] == "yp"
    assert v["ganglios_examinados"] == 18 and v["ganglios_positivos"] == 2
    assert v["margem_r"] == "R0"
    assert v["invasao_linfovascular"] is True
    assert v["invasao_perineural"] is False
    # análises: regra temporal
    assert v["cea_preop"] == 7.2
    assert v["hemoglobina_preop"] == 11.8            # não apanha HGM nem HbA1c
    assert v["pcr_max_pos_op"] == 18.6               # máximo pós-op (dia 2), não o pré-op
    assert r.campos["ca19_9_preop"].estado == REVER  # "< 2,0" deve ir para revisão
    assert not any("⚠" in a for a in r.avisos)


def test_aviso_processos_diferentes_na_pasta(tmp_path, cfg):
    d = tmp_path / "HCD001"
    d.mkdir()
    shutil.copy(FIX / "ap_reto.txt", d / "AP.txt")
    (d / "cirurgia.txt").write_text("RELATÓRIO OPERATÓRIO\nProcesso: 5555555\nCirurgião: X", encoding="utf-8")
    r = processar_doente(d, cfg, llm=None)
    assert any("DIFERENTES" in a for a in r.avisos)


def test_tabela_correspondencia(tmp_path, cfg):
    from extrator.motor import carregar_correspondencia

    d = tmp_path / "HCD001"
    d.mkdir()
    shutil.copy(FIX / "ap_reto.txt", d / "AP.txt")           # processo 1234567
    tab = tmp_path / "correspondencia.csv"
    tab.write_text("patient_id;processo\nHCD001;1234567\nHCD002;7654321\n", encoding="utf-8")
    corr = carregar_correspondencia(tab)
    assert not any("⚠" in a for a in processar_doente(d, cfg, correspondencia=corr).avisos)
    tab.write_text("patient_id;processo\nHCD001;7654321\n", encoding="utf-8")
    corr = carregar_correspondencia(tab)
    assert any("não corresponde" in a for a in processar_doente(d, cfg, correspondencia=corr).avisos)


# ------------------------------------------------------------ LLM simulado
class RespostaFalsa:
    def __init__(self, dados, status=200):
        self._d, self.status_code, self.text = dados, status, ""

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_llm_hibrido_discordancia(pasta_doente, cfg, monkeypatch):
    def falso_post(url, json=None, timeout=None):
        props = json["format"]["properties"]
        if "pT" in props:  # anatomia patológica
            resp = {k: {"valor": None, "citacao": ""} for k in props}
            resp["pT"] = {"valor": "T3", "citacao": "ypT3 ypN1b"}
            resp["pN"] = {"valor": "N1a", "citacao": "ypN1b (2/18)"}             # discorda do regex
            resp["tipo_histologico"] = {"valor": "adenocarcinoma", "citacao": "Adenocarcinoma moderadamente diferenciado"}
            resp["estado_mmr"] = {"valor": "pMMR", "citacao": "texto inventado que não existe"}  # alucinação
        else:
            resp = {k: {"valor": None, "citacao": ""} for k in props}
            resp["procedimento"] = {"valor": "resseção anterior do reto",
                                    "citacao": "Resseção anterior do reto por via laparoscópica"}
        import json as _j
        return RespostaFalsa({"message": {"content": _j.dumps(resp)}})

    monkeypatch.setattr("extrator.llm.requests.post", falso_post)
    llm = ClienteLLM(ConfigLLM())
    r = processar_doente(pasta_doente, cfg, llm=llm)
    assert r.campos["pT"].estado == OK and r.campos["pT"].fonte == "regex+llm"
    assert r.campos["pN"].estado == REVER and r.campos["pN"].valor == "N1b"   # prioridade regex
    assert r.campos["tipo_histologico"].estado == OK
    assert r.campos["estado_mmr"].estado == REVER                               # citação não verificada
    assert r.campos["procedimento"].valor == "resseção anterior do reto"


def test_llm_recusa_url_remota():
    with pytest.raises(ValueError):
        ClienteLLM(ConfigLLM(url="https://api.exemplo.com"))


def test_schema_llm(cfg):
    s = construir_schema([c for c in cfg.campos if c.usa_llm])
    valor = s["properties"]["abordagem"]["properties"]["valor"]
    assert valor["anyOf"][0]["enum"] == ["laparoscopica", "robotica", "aberta", "convertida"]
    assert valor["anyOf"][1] == {"type": "null"}
    json.dumps(s)


def test_verificar_citacao():
    t = "Adenocarcinoma moderadamente diferenciado (G2), após terapêutica."
    assert verificar_citacao("adenocarcinoma moderadamente  diferenciado", t)
    assert not verificar_citacao("carcinoma de células em anel de sinete", t)


# ------------------------------------------------------- revisão + BD Excel
def test_revisao_e_preenchimento(pasta_doente, cfg, tmp_path):
    r = processar_doente(pasta_doente, cfg, llm=None)
    rev = exportar_revisao([r], cfg, tmp_path / "revisao.xlsx")

    # simular revisão humana: aprovar tudo o que tem valor, corrigir pM
    wb = load_workbook(rev)
    ws = wb["Revisao"]
    cab = [c.value for c in ws[1]]
    ic, iv, ia = cab.index("campo") + 1, cab.index("valor") + 1, cab.index("aprovar") + 1
    for row in range(2, ws.max_row + 1):
        if ws.cell(row, iv).value not in (None, ""):
            ws.cell(row, ia).value = "S"
        if ws.cell(row, ic).value == "duracao_cirurgia_min":
            ws.cell(row, iv).value = 250  # correção manual
    wb.save(rev)

    # BD existente: 1 doente com perdas já preenchidas (valor diferente) e outro doente
    bd = tmp_path / "bd.xlsx"
    wbd = Workbook()
    s = wbd.active
    s.title = "Doentes"
    s.append(["Processo", "Data cirurgia", "Abordagem", "Perdas hemáticas (mL)", "Duração (min)",
              "pT", "pN", "Gânglios positivos", "ILV", "CEA pré-op", "Estoma"])
    s.append([1111111])
    s.append([1234567, None, None, 200])
    wbd.save(bd)

    cfg_bd = ConfigBaseDados(
        ficheiro=str(bd), folha="Doentes", coluna_id="Processo",
        mapeamento={"data_cirurgia": "Data cirurgia", "abordagem": "Abordagem",
                    "perdas_hematicas_ml": "Perdas hemáticas (mL)", "duracao_cirurgia_min": "Duração (min)",
                    "pT": "pT", "pN": "pN", "ganglios_positivos": "Gânglios positivos",
                    "invasao_linfovascular": "ILV", "cea_preop": "CEA pré-op", "estoma": "Estoma",
                    "margem_r": "Coluna inexistente"},
        recodificacao={"abordagem": {"laparoscopica": 1, "aberta": 2}, "_booleano": {True: 1, False: 0}},
    )

    # simulação não altera o ficheiro
    antes = bd.read_bytes()
    res = preencher_base_dados(ler_revisao(rev), cfg, cfg_bd, simular=True, pasta_registo=tmp_path)
    assert bd.read_bytes() == antes and res["backup"] is None

    res = preencher_base_dados(ler_revisao(rev), cfg, cfg_bd, pasta_registo=tmp_path)
    assert Path(res["backup"]).exists()
    assert res["contagem"]["conflitos"] == 1          # perdas 200 vs 150 não é sobrescrito
    assert "margem_r" in res["campos_sem_coluna"]

    s = load_workbook(bd)["Doentes"]
    linha = {s.cell(1, c).value: s.cell(3, c).value for c in range(1, s.max_column + 1)}
    assert linha["Processo"] == 1234567
    assert linha["Data cirurgia"] == datetime(2025, 3, 15)
    assert linha["Abordagem"] == 1
    assert linha["Perdas hemáticas (mL)"] == 200      # preservado
    assert linha["Duração (min)"] == 250              # correção manual respeitada
    assert linha["pT"] == "T3" and linha["pN"] == "N1b"
    assert linha["Gânglios positivos"] == 2
    assert linha["ILV"] == 1
    assert linha["CEA pré-op"] == 7.2
    assert s.cell(2, 2).value is None                  # outro doente intacto


# ------------------------------------------- modo rápido e comando testar-llm
def _ollama_falso(monkeypatch, chamadas):
    import json as _j

    def falso_get(url, timeout=None):
        return RespostaFalsa({"models": [{"name": "qwen2.5:3b"}]})

    def falso_post(url, json=None, timeout=None):
        props = list(json["format"]["properties"])
        chamadas.append(props)
        resp = {k: {"valor": None, "citacao": ""} for k in props}
        if "pT" in props:
            resp["pT"] = {"valor": "T3", "citacao": "ypT3"}
        if "estado_mmr" in props:
            resp["estado_mmr"] = {"valor": "pMMR", "citacao": "(pMMR)"}
        return RespostaFalsa({"message": {"content": _j.dumps(resp)}})

    monkeypatch.setattr("extrator.llm.requests.get", falso_get)
    monkeypatch.setattr("extrator.llm.requests.post", falso_post)


def test_llm_so_se_regex_falhar(pasta_doente, cfg, monkeypatch):
    chamadas = []
    _ollama_falso(monkeypatch, chamadas)
    llm = ClienteLLM(ConfigLLM(llm_em_hibridos="se_regex_falhar"))
    r = processar_doente(pasta_doente, cfg, llm=llm)
    pedidos_ap = [c for c in chamadas if "estado_mmr" in c][0]
    assert "pT" not in pedidos_ap and "ganglios_positivos" not in pedidos_ap   # regex já encontrou
    assert "tipo_histologico" in pedidos_ap                                    # campo só-LLM
    assert r.campos["pT"].valor == "T3" and r.campos["pT"].fonte == "regex"
    assert r.campos["estado_mmr"].valor == "pMMR"


def test_comando_testar_llm(monkeypatch, capsys):
    from extrator.cli import main

    _ollama_falso(monkeypatch, [])
    assert main(["testar-llm"]) == 0
    out = capsys.readouterr().out
    assert "Acerto:" in out and "qwen2.5:3b" in out
