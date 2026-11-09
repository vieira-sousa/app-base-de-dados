"""Carregamento e validação da configuração (campos.yaml, base_dados.yaml)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .analises import REGRAS
from .regras import REGISTO
from .utils import parse_data, parse_numero, sem_acentos

TipoCampo = Literal["texto", "inteiro", "decimal", "data", "booleano", "categoria"]
TipoDoc = Literal["anatomia_patologica", "analises", "cirurgia"]

RAIZ = Path(__file__).resolve().parent.parent
CONFIG_PADRAO = RAIZ / "config"


class Campo(BaseModel):
    nome: str
    descricao: str = ""
    tipo: TipoCampo = "texto"
    documento: TipoDoc
    metodo: Literal["regex", "llm", "hibrido"] = "hibrido"
    regex: str | None = None
    padrao: str | None = None
    mapa: dict[str, Any] | None = None
    opcoes: list[str] | None = None
    prioridade: Literal["regex", "llm"] = "regex"

    @field_validator("nome")
    @classmethod
    def _nome_valido(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"nome de campo inválido '{v}' (use letras, números e _)")
        return v

    @model_validator(mode="after")
    def _coerente(self):
        if self.metodo in ("regex", "hibrido") and not (self.regex or self.padrao):
            raise ValueError(f"campo '{self.nome}': metodo={self.metodo} exige 'regex' ou 'padrao'")
        if self.regex and self.regex not in REGISTO:
            raise ValueError(f"campo '{self.nome}': extrator regex '{self.regex}' não existe. "
                             f"Disponíveis: {', '.join(sorted(REGISTO))}")
        if self.padrao:
            try:
                rx = re.compile(self.padrao)
            except re.error as e:
                raise ValueError(f"campo '{self.nome}': padrao inválido: {e}") from e
            if rx.groups < 1:
                raise ValueError(f"campo '{self.nome}': padrao precisa de um grupo de captura (...)")
        if self.tipo == "categoria" and not self.opcoes:
            raise ValueError(f"campo '{self.nome}': tipo categoria exige 'opcoes'")
        return self

    @property
    def usa_regex(self) -> bool:
        return self.metodo in ("regex", "hibrido")

    @property
    def usa_llm(self) -> bool:
        return self.metodo in ("llm", "hibrido")


class ParametroAnalise(BaseModel):
    nome: str
    aliases: list[str] = Field(min_length=1)
    unidade: str | None = None
    regra: str = "ultimo_antes_cirurgia"
    janela_dias: int | None = None

    @field_validator("regra")
    @classmethod
    def _regra(cls, v: str) -> str:
        if v not in REGRAS:
            raise ValueError(f"regra '{v}' inválida. Opções: {', '.join(sorted(REGRAS))}")
        return v


class ConfigCampos(BaseModel):
    campos: list[Campo]
    analises: list[ParametroAnalise] = []

    @model_validator(mode="after")
    def _unicos(self):
        nomes = [c.nome for c in self.campos] + [a.nome for a in self.analises]
        dup = {n for n in nomes if nomes.count(n) > 1}
        if dup:
            raise ValueError(f"nomes de campo repetidos: {', '.join(sorted(dup))}")
        return self

    def nomes(self) -> list[str]:
        return [c.nome for c in self.campos] + [a.nome for a in self.analises]

    def tipo_de(self, nome: str) -> str:
        for c in self.campos:
            if c.nome == nome:
                return c.tipo
        return "decimal"  # análises


class ConfigLLM(BaseModel):
    ativo: bool = True
    url: str = "http://localhost:11434"
    modelo: str = "qwen2.5:7b"
    temperatura: float = 0.0
    contexto_tokens: int = 8192
    max_caracteres_documento: int = 20000
    timeout_s: int = 600


class ConfigBaseDados(BaseModel):
    ficheiro: str
    folha: str | None = None
    linha_cabecalho: int = 1
    coluna_id: str
    mapeamento: dict[str, str]
    recodificacao: dict[str, dict[Any, Any]] = {}
    sobrescrever: bool = False
    criar_linhas_novas: bool = True


def carregar_campos(caminho: str | Path | None = None) -> ConfigCampos:
    caminho = Path(caminho or CONFIG_PADRAO / "campos.yaml")
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    return ConfigCampos(**dados)


def carregar_llm(caminho: str | Path | None = None) -> ConfigLLM:
    caminho = Path(caminho or CONFIG_PADRAO / "llm.yaml")
    if not caminho.exists():
        return ConfigLLM()
    return ConfigLLM(**(yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}))


def carregar_base_dados(caminho: str | Path | None = None) -> ConfigBaseDados:
    caminho = Path(caminho or CONFIG_PADRAO / "base_dados.yaml")
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Copie config/base_dados.exemplo.yaml para config/base_dados.yaml e ajuste.")
    cfg = ConfigBaseDados(**(yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}))
    f = Path(cfg.ficheiro).expanduser()
    if not f.is_absolute():
        cfg.ficheiro = str((caminho.parent / f).resolve())  # relativo à pasta do YAML
    return cfg


# --------------------------------------------------------------- coerção
VERDADEIRO = {"sim", "s", "true", "verdadeiro", "presente", "1", "yes", "positivo", "positiva"}
FALSO = {"nao", "n", "false", "falso", "ausente", "0", "no", "negativo", "negativa"}


def coagir(campo_tipo: str, valor: Any, opcoes: list[str] | None = None) -> tuple[Any, str]:
    """Converte um valor para o tipo do campo. Devolve (valor, erro)."""
    if valor is None or (isinstance(valor, str) and valor.strip().lower() in ("", "null", "none", "n/a", "nd")):
        return None, ""
    try:
        if campo_tipo == "texto":
            return str(valor).strip(), ""
        if campo_tipo == "inteiro":
            n = parse_numero(valor)
            if n is None:
                return None, f"não é número: {valor!r}"
            if not float(n).is_integer():
                return round(n), f"arredondado de {n}"
            return int(n), ""
        if campo_tipo == "decimal":
            n = parse_numero(valor)
            return (n, "") if n is not None else (None, f"não é número: {valor!r}")
        if campo_tipo == "data":
            d = parse_data(valor) if not isinstance(valor, date) else valor
            return (d, "") if d else (None, f"data inválida: {valor!r}")
        if campo_tipo == "booleano":
            if isinstance(valor, bool):
                return valor, ""
            s = sem_acentos(str(valor)).strip().lower()
            if s in VERDADEIRO:
                return True, ""
            if s in FALSO:
                return False, ""
            return None, f"booleano inválido: {valor!r}"
        if campo_tipo == "categoria":
            s = str(valor).strip()
            if opcoes:
                alvo = {sem_acentos(o).lower(): o for o in opcoes}
                chave = sem_acentos(s).lower()
                if chave in alvo:
                    return alvo[chave], ""
                return None, f"'{s}' não está nas opções {opcoes}"
            return s, ""
    except Exception as e:  # pragma: no cover - defensivo
        return None, str(e)
    return valor, ""
