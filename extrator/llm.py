"""Cliente para LLM local via Ollama (http://localhost:11434). Nada sai do computador.

Usa "structured outputs" do Ollama: o modelo é obrigado a responder com JSON
que cumpre o esquema gerado a partir de campos.yaml.
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

import requests

from .esquema import Campo, ConfigLLM
from .regras import Candidato
from .utils import normalizar

log = logging.getLogger(__name__)

NOMES_DOC = {
    "anatomia_patologica": "relatório de anatomia patológica",
    "analises": "relatório de análises clínicas",
    "cirurgia": "relatório operatório",
}

SISTEMA = (
    "És um assistente de extração de dados clínicos para uma base de dados de investigação "
    "de cirurgia geral num hospital português. Regras:\n"
    "1. Extrai APENAS informação explicitamente escrita no documento. Nunca deduzas nem inventes.\n"
    "2. Se a informação não estiver no documento, usa valor null e citacao \"\".\n"
    "3. Em 'citacao' copia literalmente um excerto curto (máx. 100 caracteres) do documento que suporta o valor.\n"
    "4. Datas no formato AAAA-MM-DD. Números sem unidades, com ponto decimal.\n"
    "5. Em campos de categoria usa exatamente uma das opções permitidas.\n"
    "6. Se houver vários tumores/peças, refere-te à lesão principal (maior estadio) e explica em 'citacao'."
)


def _local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _schema_valor(c: Campo) -> dict:
    # formato igual ao de Optional[...] em Pydantic (o que o Ollama documenta)
    if c.tipo == "inteiro":
        base = {"type": "integer"}
    elif c.tipo == "decimal":
        base = {"type": "number"}
    elif c.tipo == "booleano":
        base = {"type": "boolean"}
    elif c.tipo == "categoria":
        base = {"type": "string", "enum": list(c.opcoes or [])}
    else:
        base = {"type": "string"}
    return {"anyOf": [base, {"type": "null"}]}


def construir_schema(campos: list[Campo]) -> dict:
    props = {
        c.nome: {
            "type": "object",
            "properties": {"valor": _schema_valor(c), "citacao": {"type": "string"}},
            "required": ["valor", "citacao"],
        }
        for c in campos
    }
    return {"type": "object", "properties": props, "required": [c.nome for c in campos]}


def construir_prompt(texto: str, tipo_doc: str, campos: list[Campo]) -> str:
    linhas = []
    for c in campos:
        extra = f" Opções: {', '.join(c.opcoes)}." if c.opcoes else ""
        linhas.append(f"- {c.nome} ({c.tipo}): {c.descricao}{extra}")
    return (
        f"Documento: {NOMES_DOC.get(tipo_doc, tipo_doc)}.\n\n"
        f"Campos a extrair:\n" + "\n".join(linhas) +
        "\n\n=== INÍCIO DO DOCUMENTO ===\n" + texto + "\n=== FIM DO DOCUMENTO ===\n\n"
        "Responde só com o JSON."
    )


def verificar_citacao(citacao: str, texto: str) -> bool:
    """A citação existe (aproximadamente) no texto original? Protege contra alucinações."""
    c = normalizar(citacao).strip(" .…\"'")
    if not c:
        return False
    t = normalizar(texto)
    if c in t:
        return True
    # tolerância a pequenas diferenças (OCR, espaços, hífens)
    if len(c) < 8:
        return False
    sm = SequenceMatcher(None, t, c, autojunk=False)
    bloco = sm.find_longest_match(0, len(t), 0, len(c))
    if bloco.size >= 0.6 * len(c):
        return True
    janela = t[max(0, bloco.a - bloco.b): bloco.a - bloco.b + len(c) + 10]
    return SequenceMatcher(None, janela, c, autojunk=False).ratio() >= 0.85


class ClienteLLM:
    def __init__(self, cfg: ConfigLLM):
        self.cfg = cfg
        if not _local(cfg.url):
            # salvaguarda RGPD: só aceitamos servidores locais
            raise ValueError(f"URL do LLM não é local ({cfg.url}). Por segurança só é permitido localhost.")

    def disponivel(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.cfg.url}/api/tags", timeout=5)
            r.raise_for_status()
        except Exception as e:
            return False, f"Ollama não está a correr em {self.cfg.url} ({e.__class__.__name__}). Iniciar com: ollama serve"
        modelos = [m.get("name", "") for m in r.json().get("models", [])]
        base = self.cfg.modelo if ":" in self.cfg.modelo else self.cfg.modelo + ":latest"
        if base not in modelos and self.cfg.modelo not in modelos:
            return False, f"Modelo '{self.cfg.modelo}' não instalado. Instalar com: ollama pull {self.cfg.modelo}"
        return True, "ok"

    def _chamar(self, prompt: str, schema: dict) -> dict:
        corpo = {
            "model": self.cfg.modelo,
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": self.cfg.temperatura, "num_ctx": self.cfg.contexto_tokens, "seed": 42},
        }
        if self.cfg.num_gpu is not None:
            corpo["options"]["num_gpu"] = self.cfg.num_gpu
        if self.cfg.num_threads is not None:
            corpo["options"]["num_thread"] = self.cfg.num_threads
        r = requests.post(f"{self.cfg.url}/api/chat", json=corpo, timeout=self.cfg.timeout_s)
        if r.status_code == 400 and "think" in r.text:
            corpo.pop("think")  # modelos/versões sem suporte para 'think'
            r = requests.post(f"{self.cfg.url}/api/chat", json=corpo, timeout=self.cfg.timeout_s)
        r.raise_for_status()
        conteudo = r.json()["message"]["content"]
        return _parse_json(conteudo)

    def extrair(self, texto: str, tipo_doc: str, campos: list[Campo], ficheiro: str = "") -> dict[str, Candidato]:
        if not campos:
            return {}
        texto_llm = texto
        if len(texto_llm) > self.cfg.max_caracteres_documento:
            log.warning("%s: texto truncado de %d para %d caracteres", ficheiro, len(texto), self.cfg.max_caracteres_documento)
            texto_llm = texto_llm[: self.cfg.max_caracteres_documento]
        resposta = self._chamar(construir_prompt(texto_llm, tipo_doc, campos), construir_schema(campos))
        saida: dict[str, Candidato] = {}
        for c in campos:
            item = resposta.get(c.nome)
            if not isinstance(item, dict):
                continue
            valor = item.get("valor")
            if valor is None:
                continue
            citacao = str(item.get("citacao") or "")
            ok = verificar_citacao(citacao, texto) if citacao else False
            saida[c.nome] = Candidato(
                valor, citacao, metodo="llm", ficheiro=ficheiro, verificado=ok,
                nota="" if ok else "citação do LLM não encontrada no documento",
            )
        return saida


def _parse_json(conteudo: str) -> dict[str, Any]:
    try:
        return json.loads(conteudo)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", conteudo, re.S)
        if m:
            return json.loads(m.group(0))
        raise
