"""Carregamento e validação da configuração (campos.yaml, base_dados.yaml)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .analises import REGRAS
from .derivados import DERIVADOS, NAO_APLICAVEL, parse_chamada, validar_condicao
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
    # se o documento principal não tiver o valor, tentar a regex neste (ex. localização no relatório operatório)
    documento_alternativo: TipoDoc | None = None
    metodo: Literal["regex", "llm", "hibrido", "derivado"] = "hibrido"
    regex: str | None = None
    padrao: str | None = None
    mapa: dict[str, Any] | None = None
    opcoes: list[str] | None = None
    prioridade: Literal["regex", "llm"] = "regex"
    # calculado a partir de outros campos (usado se a extração direta não der valor)
    derivado: str | None = None
    # condições em que o campo é "não aplicável" (ex.: "approach == aberta")
    nao_aplicavel_se: list[str] = []

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
        if self.metodo == "derivado" and not self.derivado:
            raise ValueError(f"campo '{self.nome}': metodo=derivado exige 'derivado'")
        if self.derivado and parse_chamada(self.derivado)[0] not in DERIVADOS:
            raise ValueError(f"campo '{self.nome}': derivado '{self.derivado}' não existe. "
                             f"Disponíveis: {', '.join(sorted(DERIVADOS))}")
        for cond in self.nao_aplicavel_se:
            validar_condicao(cond)
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
    # fator para converter da unidade do relatório para a unidade da BD (ex. PCR mg/dL -> mg/L: 10)
    conversoes: dict[str, float] = {}
    # unidades a ignorar (ex. "%" para neutrófilos: queremos o valor absoluto)
    excluir_unidades: list[str] = []
    # intervalo plausível na unidade da BD; fora disto -> rever
    intervalo: list[float] | None = None

    @field_validator("regra")
    @classmethod
    def _regra(cls, v: str) -> str:
        if v not in REGRAS:
            raise ValueError(f"regra '{v}' inválida. Opções: {', '.join(sorted(REGRAS))}")
        return v


class ConfigCampos(BaseModel):
    campos: list[Campo]
    analises: list[ParametroAnalise] = []
    campo_data_cirurgia: str = "data_cirurgia"   # usado pelas regras temporais das análises
    # regex que a AP tem de conter para o doente ser elegível (ex. "carcinoma"); senão gera aviso
    elegibilidade_ap: str | None = None

    @model_validator(mode="after")
    def _unicos(self):
        nomes = [c.nome for c in self.campos] + [a.nome for a in self.analises]
        dup = {n for n in nomes if nomes.count(n) > 1}
        if dup:
            raise ValueError(f"nomes de campo repetidos: {', '.join(sorted(dup))}")
        return self

    def campos_ordem(self) -> list[str]:
        return [c.nome for c in self.campos]

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
    modelo: str = "qwen2.5:3b"
    temperatura: float = 0.0
    contexto_tokens: int = 8192
    max_caracteres_documento: int = 16000
    timeout_s: int = 900
    num_gpu: int | None = None        # 0 = forçar CPU; None = Ollama decide
    num_threads: int | None = None    # None = Ollama decide
    llm_em_hibridos: Literal["sempre", "se_regex_falhar"] = "sempre"


class ConfigBaseDados(BaseModel):
    ficheiro: str
    folha: str | None = None
    linha_cabecalho: int = 1
    coluna_id: str
    # "auto": cada campo vai para a coluna com o mesmo nome (aceita sufixos, ex. "operative_time_min_Difícil")
    mapeamento: dict[str, str] | Literal["auto"] = "auto"
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
    if isinstance(valor, str) and valor.strip().lower() in (NAO_APLICAVEL, "nao aplicavel", "não aplicável", "na"):
        return NAO_APLICAVEL, ""
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
