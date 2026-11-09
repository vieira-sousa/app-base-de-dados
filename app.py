"""Interface gráfica local.   Iniciar com:  streamlit run app.py

Corre só em http://localhost:8501 (ver .streamlit/config.toml). Nada sai do computador.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from extrator.esquema import CONFIG_PADRAO, carregar_base_dados, carregar_campos, carregar_llm
from extrator.exportar import (exportar_revisao, guardar_json, guardar_revisao_editada, ler_revisao,
                               ler_revisao_completa, preencher_base_dados)
from extrator.llm import ClienteLLM
from extrator.motor import OK, REVER, SEM_DADOS, processar_doente
from extrator.pdf_texto import ocr_disponivel

RAIZ = Path(__file__).resolve().parent
st.set_page_config(page_title="Extrator clínico", layout="wide")
st.title("Extrator de dados clínicos — local")
st.caption("PDFs de anatomia patológica, análises e relatórios operatórios → base de dados. Tudo processado neste computador.")

# ------------------------------------------------------------ barra lateral
with st.sidebar:
    st.header("Configuração")
    try:
        cfg = carregar_campos()
        st.success(f"{len(cfg.campos)} campos + {len(cfg.analises)} análises")
    except Exception as e:
        st.error(f"config/campos.yaml inválido:\n\n{e}")
        st.stop()
    cfg_llm = carregar_llm()
    usar_llm = st.toggle("Usar LLM local (Ollama)", value=cfg_llm.ativo)
    cfg_llm.modelo = st.text_input("Modelo", cfg_llm.modelo)
    llm = None
    if usar_llm:
        cliente = ClienteLLM(cfg_llm)
        ok, msg = cliente.disponivel()
        if ok:
            st.success(f"LLM pronto: {cfg_llm.modelo}")
            llm = cliente
        else:
            st.warning(msg + "\n\nA extração continua só com regex.")
    st.write(("✅" if ocr_disponivel() else "⚠️") + " OCR " + ("disponível" if ocr_disponivel() else "não instalado"))

aba1, aba2, aba3 = st.tabs(["1 · Extrair", "2 · Rever", "3 · Preencher BD"])

# ------------------------------------------------------------------ extrair
with aba1:
    st.markdown("Estrutura: **uma subpasta por doente**, com o nº de processo como nome, contendo os PDFs.")
    st.code("entrada/\n  1234567/\n    AP_peca.pdf\n    analises_preop.pdf\n    relatorio_operatorio.pdf\n  7654321/\n    ...")
    entrada = st.text_input("Pasta de entrada", str(RAIZ / "entrada"))
    saida = st.text_input("Pasta de resultados", str(RAIZ / "resultados"))
    pasta = Path(entrada)
    doentes = sorted(p for p in pasta.iterdir() if p.is_dir() and not p.name.startswith(".")) if pasta.is_dir() else []
    st.write(f"{len(doentes)} doente(s) encontrados.")
    if st.button("Extrair", type="primary", disabled=not doentes):
        barra = st.progress(0.0)
        estado = st.empty()
        resultados = []
        for i, d in enumerate(doentes):
            resultados.append(processar_doente(d, cfg, llm, progresso=lambda m: estado.write(m)))
            barra.progress((i + 1) / len(doentes))
        guardar_json(resultados, Path(saida) / "json")
        caminho = exportar_revisao(resultados, cfg, Path(saida) / f"revisao_{datetime.now():%Y%m%d_%H%M}.xlsx")
        st.session_state["revisao"] = str(caminho)
        estado.empty()
        contagem = {OK: 0, REVER: 0, SEM_DADOS: 0}
        for r in resultados:
            for c in r.campos.values():
                contagem[c.estado] = contagem.get(c.estado, 0) + 1
        a, b, c = st.columns(3)
        a.metric("OK", contagem[OK])
        b.metric("A rever", contagem[REVER])
        c.metric("Sem dados", contagem[SEM_DADOS])
        avisos = [(r.id_doente, a) for r in resultados for a in r.avisos]
        if avisos:
            st.warning("\n".join(f"- **{i}**: {a}" for i, a in avisos))
        st.success(f"Folha de revisão criada: {caminho}. Passe ao separador **2 · Rever**.")

# -------------------------------------------------------------------- rever
with aba2:
    pasta_res = Path(st.session_state.get("revisao", RAIZ / "resultados" / "x")).parent
    ficheiros = sorted(pasta_res.glob("revisao_*.xlsx"), reverse=True) if pasta_res.is_dir() else []
    if not ficheiros:
        st.info("Ainda não há folhas de revisão. Corra a extração primeiro.")
    else:
        escolhido = st.selectbox("Folha de revisão", ficheiros, format_func=lambda p: p.name,
                                 index=ficheiros.index(Path(st.session_state["revisao"])) if st.session_state.get("revisao") and Path(st.session_state["revisao"]) in ficheiros else 0)
        linhas = ler_revisao_completa(escolhido)
        df = pd.DataFrame(linhas)
        df["valor"] = [
            "" if v is None or (isinstance(v, float) and pd.isna(v))
            else v.strftime("%d/%m/%Y") if isinstance(v, datetime) else str(v)
            for v in df["valor"]
        ]
        df["aprovar"] = df["aprovar"].fillna("")
        filtro = st.multiselect("Mostrar estados", [OK, REVER, SEM_DADOS], default=[REVER, OK])
        ids = st.multiselect("Doentes", sorted(df["id_doente"].astype(str).unique()))
        mascara = df["estado"].isin(filtro)
        if ids:
            mascara &= df["id_doente"].astype(str).isin(ids)
        st.caption("Edite **valor** e marque **aprovar = S**. Só as linhas com S vão para a base de dados.")
        editado = st.data_editor(
            df[mascara],
            column_config={
                "aprovar": st.column_config.SelectboxColumn("aprovar", options=["S", "N", ""]),
                "evidencia": st.column_config.TextColumn("evidência", width="large"),
            },
            disabled=[c for c in df.columns if c not in ("valor", "aprovar")],
            width="stretch", height=520, key=f"editor_{escolhido.name}",
        )
        c1, c2 = st.columns(2)
        if c1.button("Aprovar todos os 'ok' visíveis"):
            df.loc[mascara & (df["estado"] == OK), "aprovar"] = "S"
            guardar_revisao_editada(escolhido, df.to_dict("records"))
            st.rerun()
        if c2.button("Guardar alterações", type="primary"):
            df.loc[editado.index, ["valor", "aprovar"]] = editado[["valor", "aprovar"]]
            guardar_revisao_editada(escolhido, df.to_dict("records"))
            st.success("Guardado.")
        st.session_state["revisao"] = str(escolhido)

# ---------------------------------------------------------------- preencher
with aba3:
    caminho_cfg = CONFIG_PADRAO / "base_dados.yaml"
    if not caminho_cfg.exists():
        st.warning("Falta config/base_dados.yaml — copie config/base_dados.exemplo.yaml e ajuste às colunas da sua BD.")
    elif not st.session_state.get("revisao"):
        st.info("Escolha uma folha de revisão no separador 2.")
    else:
        try:
            cfg_bd = carregar_base_dados(caminho_cfg)
        except Exception as e:
            st.error(str(e))
            st.stop()
        st.write(f"Base de dados: `{cfg_bd.ficheiro}` · sobrescrever: **{cfg_bd.sobrescrever}**")
        st.write(f"Revisão: `{st.session_state['revisao']}`")
        rev = ler_revisao(st.session_state["revisao"])
        st.write(f"{sum(1 for r in rev if str(r.get('aprovar') or '').upper() == 'S')} valores aprovados.")
        col1, col2 = st.columns(2)
        for simular, col, rotulo in ((True, col1, "Simular"), (False, col2, "Escrever na BD (faz backup)")):
            if col.button(rotulo, type="secondary" if simular else "primary"):
                try:
                    r = preencher_base_dados(rev, cfg, cfg_bd, simular=simular,
                                             pasta_registo=Path(st.session_state["revisao"]).parent)
                except Exception as e:
                    st.error(str(e))
                else:
                    st.json(r)
                    st.dataframe(pd.read_excel(r["registo"]), width="stretch")
