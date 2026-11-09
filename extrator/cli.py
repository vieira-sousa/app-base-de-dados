"""Linha de comandos.

  python -m extrator verificar
  python -m extrator extrair entrada/ [--sem-llm] [--saida resultados/]
  python -m extrator preencher resultados/revisao.xlsx [--simular]
  python -m extrator testar ficheiro.pdf [--sem-llm]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .classificador import classificar
from .esquema import carregar_base_dados, carregar_campos, carregar_llm
from .exportar import exportar_revisao, guardar_json, ler_revisao, preencher_base_dados
from .llm import ClienteLLM
from .motor import OK, REVER, processar_doente, processar_lote
from .pdf_texto import ler_documento, ocr_disponivel


def _cliente_llm(args) -> ClienteLLM | None:
    cfg = carregar_llm(args.config_llm)
    if getattr(args, "modelo", None):
        cfg.modelo = args.modelo
    if args.sem_llm or not cfg.ativo:
        print("LLM desativado — só regex.")
        return None
    cliente = ClienteLLM(cfg)
    ok, msg = cliente.disponivel()
    if not ok:
        print(f"AVISO: {msg}\n       A continuar só com regex (use --sem-llm para omitir este aviso).")
        return None
    print(f"LLM local: {cfg.modelo}")
    return cliente


def cmd_verificar(args) -> int:
    print(f"extrator {__version__}")
    try:
        cfg = carregar_campos(args.campos)
        print(f"[ok] campos.yaml: {len(cfg.campos)} campos + {len(cfg.analises)} análises")
    except Exception as e:
        print(f"[ERRO] campos.yaml: {e}")
        return 1
    print(f"[{'ok' if ocr_disponivel() else '--'}] OCR (ocrmypdf) {'disponível' if ocr_disponivel() else 'não instalado (só necessário para PDFs digitalizados)'}")
    cfg_llm = carregar_llm(args.config_llm)
    ok, msg = ClienteLLM(cfg_llm).disponivel()
    print(f"[{'ok' if ok else '--'}] LLM {cfg_llm.modelo}: {msg}")
    try:
        bd = carregar_base_dados(args.config_bd)
        existe = Path(bd.ficheiro).exists()
        print(f"[{'ok' if existe else 'ERRO'}] base de dados: {bd.ficheiro}{'' if existe else ' (não encontrada)'}")
    except Exception as e:
        print(f"[--] base de dados: {e}")
    return 0


def cmd_extrair(args) -> int:
    cfg = carregar_campos(args.campos)
    llm = _cliente_llm(args)
    entrada = Path(args.entrada)
    saida = Path(args.saida)

    def prog(msg):
        print("  " + msg, flush=True)

    # aceita a pasta-mãe (várias subpastas) ou a pasta de um único doente
    tem_subpastas = any(p.is_dir() and not p.name.startswith(".") for p in entrada.iterdir())
    from .esquema import RAIZ
    from .motor import carregar_correspondencia

    caminho_corr = Path(args.correspondencia) if args.correspondencia else RAIZ / "dados" / "correspondencia.csv"
    corr = carregar_correspondencia(caminho_corr) if caminho_corr.exists() else None
    print(f"Tabela de correspondência: {caminho_corr if corr is not None else 'não usada'}")
    if tem_subpastas:
        resultados = processar_lote(entrada, cfg, llm, not args.sem_ocr, prog, corr)
    else:
        resultados = [processar_doente(entrada, cfg, llm, not args.sem_ocr, prog, corr)]

    guardar_json(resultados, saida / "json")
    carimbo = datetime.now().strftime("%Y%m%d_%H%M")
    caminho = exportar_revisao(resultados, cfg, saida / f"revisao_{carimbo}.xlsx")

    total = sum(len(r.campos) for r in resultados)
    n_ok = sum(1 for r in resultados for c in r.campos.values() if c.estado == OK)
    n_rev = sum(1 for r in resultados for c in r.campos.values() if c.estado == REVER)
    print(f"\n{len(resultados)} doente(s) | {total} campos: {n_ok} ok, {n_rev} a rever, {total - n_ok - n_rev} sem dados")
    for r in resultados:
        for a in r.avisos:
            print(f"  [{r.id_doente}] {a}")
    print(f"\nRevisão: {caminho}\nAbra, valide (coluna 'aprovar' = S) e depois:\n"
          f"  python -m extrator preencher {caminho} --simular")
    return 0


def cmd_preencher(args) -> int:
    cfg = carregar_campos(args.campos)
    bd = carregar_base_dados(args.config_bd)
    revisao = ler_revisao(args.revisao)
    r = preencher_base_dados(revisao, cfg, bd, caminho_bd=args.bd, simular=args.simular,
                             pasta_registo=Path(args.revisao).parent)
    print(("SIMULAÇÃO — nada foi gravado.\n" if args.simular else "") + json.dumps(r, ensure_ascii=False, indent=2))
    if r["campos_sem_coluna"]:
        print("Campos sem coluna na BD (não escritos):", ", ".join(r["campos_sem_coluna"]))
    return 0


def cmd_testar(args) -> int:
    """Mostra o que é extraído de UM ficheiro — útil para afinar regex."""
    from .esquema import coagir
    from .motor import _regex_campo

    cfg = carregar_campos(args.campos)
    doc = ler_documento(args.ficheiro, usar_ocr=not args.sem_ocr)
    tipo, pts = classificar(doc.texto, doc.caminho)
    print(f"Ficheiro: {doc.nome} | texto via {doc.metodo} | {len(doc.texto)} caracteres")
    print(f"Tipo: {tipo}  {pts}")
    for a in doc.avisos:
        print("AVISO:", a)
    if args.texto:
        print("-" * 80 + "\n" + doc.texto + "\n" + "-" * 80)
    if tipo is None:
        return 1
    if tipo == "analises":
        from .analises import extrair_valores
        for v in extrair_valores(doc.texto, [a.model_dump() for a in cfg.analises], doc.nome):
            print(f"  {v.parametro:22} {v.comparador}{v.valor:<10g} {v.unidade:10} {v.data}  | {v.evidencia}")
        return 0
    campos = [c for c in cfg.campos if c.documento == tipo]
    llm = _cliente_llm(args)
    llm_res = llm.extrair(doc.texto, tipo, [c for c in campos if c.usa_llm], doc.nome) if llm else {}
    for c in campos:
        r = _regex_campo(c, doc.texto) if c.usa_regex else None
        l = llm_res.get(c.nome)
        vr = coagir(c.tipo, r.valor, c.opcoes)[0] if r else None
        vl = coagir(c.tipo, l.valor, c.opcoes)[0] if l else None
        print(f"  {c.nome:26} regex={vr!s:14} llm={vl!s:14} | {(r.evidencia if r else '') or (l.evidencia if l else '')}")
    return 0


def cmd_testar_llm(args) -> int:
    """Mede velocidade e acerto do LLM num relatório fictício incluído (tests/fixtures)."""
    import time

    from .esquema import RAIZ, coagir

    fixtures = RAIZ / "tests" / "fixtures" / "colon"
    casos = [
        ("AP_hemicolectomia.txt", "anatomia_patologica", {
            "tumor_location": "ascendente", "tumor_size_mm": 42.0, "histology": "adenocarcinoma_nos",
            "mucinous_component": True, "grade": "G2", "t_stage": "T3", "n_stage": "N1b",
            "nodes_examined": 24, "nodes_positive": 3, "tumor_deposits": True, "lymphatic_invasion": True,
            "venous_invasion": False, "perineural_invasion": True, "tumor_budding": "baixo",
            "pathological_r_status": "R0", "mmr_status": "pMMR", "organ_invaded_t4b": None}),
        ("cirurgia_hemicolectomia.txt", "cirurgia", {
            "operation_type": "hemicolectomia_direita", "emergency_surgery": False, "approach": "laparoscopica",
            "conversion": False, "blood_loss_ml": 50, "anastomosis": True, "intracorporeal_anastomosis": True,
            "ascites_intraop": False, "peritoneal_implants_intraop": False, "multivisceral_resection": None,
            "perioperative_transfusion": None, "tumor_spillage": None}),
    ]
    args.sem_llm = False
    llm = _cliente_llm(args)
    if llm is None:
        return 1
    cfg = carregar_campos(args.campos)
    acertos = total = 0
    for ficheiro, tipo, esperado in casos:
        texto = (fixtures / ficheiro).read_text(encoding="utf-8")
        campos = [c for c in cfg.campos if c.documento == tipo and c.usa_llm]
        print(f"\n{ficheiro}: {len(campos)} campos — a processar (pode demorar alguns minutos em CPU)...", flush=True)
        t0 = time.time()
        try:
            res = llm.extrair(texto, tipo, campos, ficheiro)
        except Exception as e:
            print(f"  ERRO: {e.__class__.__name__}: {e}")
            return 1
        print(f"  tempo: {time.time() - t0:.0f} s")
        for c in campos:
            if c.nome not in esperado:
                continue
            obtido = coagir(c.tipo, res[c.nome].valor, c.opcoes)[0] if c.nome in res else None
            # null esperado = o LLM não deve inventar (campo não descrito no relatório)
            certo = obtido == esperado[c.nome]
            acertos += certo
            total += 1
            print(f"  {'ok ' if certo else 'ERR'} {c.nome:24} esperado={esperado[c.nome]!s:14} obtido={obtido!s}")
    print(f"\nAcerto: {acertos}/{total} ({100 * acertos / max(total, 1):.0f}%) com {llm.cfg.modelo}")
    print("(No uso real o regex trata a maioria destes campos; isto mede só o LLM.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="extrator", description="Extração local de dados clínicos de PDFs.")
    ap.add_argument("--campos", default=None, help="caminho para campos.yaml")
    ap.add_argument("--config-llm", default=None, help="caminho para llm.yaml")
    ap.add_argument("--config-bd", default=None, help="caminho para base_dados.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("verificar", help="verifica configuração, OCR e LLM")
    s.set_defaults(fn=cmd_verificar)

    s = sub.add_parser("extrair", help="processa PDFs e gera folha de revisão")
    s.add_argument("entrada", help="pasta com uma subpasta por doente (nome = patient_id)")
    s.add_argument("--saida", default="resultados")
    s.add_argument("--sem-llm", action="store_true", help="só regex (rápido, sem Ollama)")
    s.add_argument("--sem-ocr", action="store_true")
    s.add_argument("--modelo", help="sobrepõe o modelo de llm.yaml")
    s.add_argument("--correspondencia", help="CSV patient_id;processo para verificar identidade "
                                             "(padrão: dados/correspondencia.csv, se existir)")
    s.set_defaults(fn=cmd_extrair)

    s = sub.add_parser("preencher", help="escreve valores aprovados na base de dados")
    s.add_argument("revisao", help="ficheiro revisao_*.xlsx validado")
    s.add_argument("--bd", help="sobrepõe o caminho da BD de base_dados.yaml")
    s.add_argument("--simular", action="store_true", help="não grava; só gera o registo")
    s.set_defaults(fn=cmd_preencher)

    s = sub.add_parser("testar", help="mostra o que é extraído de um único ficheiro")
    s.add_argument("ficheiro")
    s.add_argument("--texto", action="store_true", help="mostra também o texto extraído")
    s.add_argument("--sem-llm", action="store_true")
    s.add_argument("--sem-ocr", action="store_true")
    s.add_argument("--modelo")
    s.set_defaults(fn=cmd_testar)

    s = sub.add_parser("testar-llm", help="mede velocidade e acerto do LLM num relatório fictício")
    s.add_argument("--modelo", help="modelo a testar (ex.: gemma3:4b)")
    s.set_defaults(fn=cmd_testar_llm, sem_llm=False)

    # consola do Windows: evitar erros com caracteres como ≠ ou ⚠
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except AttributeError:
            pass

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
