"""Extração de texto de PDFs (com OCR opcional para PDFs digitalizados)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

EXTENSOES_SUPORTADAS = {".pdf", ".txt"}
MIN_CARACTERES_POR_PAGINA = 40  # abaixo disto assume-se PDF digitalizado (imagem)


@dataclass
class Documento:
    caminho: Path
    texto: str
    paginas: int = 0
    metodo: str = ""  # pdfplumber | pdftotext | ocr | txt
    avisos: list[str] = field(default_factory=list)
    tipo: str | None = None  # preenchido pelo classificador

    @property
    def nome(self) -> str:
        return self.caminho.name


def _pdfplumber(caminho: Path) -> tuple[str, int]:
    import pdfplumber

    partes = []
    with pdfplumber.open(caminho) as pdf:
        n = len(pdf.pages)
        for pagina in pdf.pages:
            partes.append(pagina.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
    return "\n\n".join(partes), n


def _pdftotext(caminho: Path) -> str:
    if not shutil.which("pdftotext"):
        return ""
    r = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(caminho), "-"],
        capture_output=True, text=True, timeout=120,
    )
    return r.stdout if r.returncode == 0 else ""


def ocr_disponivel() -> bool:
    return shutil.which("ocrmypdf") is not None


def _ocr(caminho: Path, idioma: str = "por") -> str:
    """OCR local via ocrmypdf + tesseract (pacote tesseract-ocr-por)."""
    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "ocr.pdf"
        r = subprocess.run(
            ["ocrmypdf", "-l", idioma, "--force-ocr", "--quiet", str(caminho), str(saida)],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"OCR falhou: {r.stderr.strip()[:300]}")
        texto, _ = _pdfplumber(saida)
        return texto


def ler_documento(caminho: str | Path, usar_ocr: bool = True) -> Documento:
    caminho = Path(caminho)
    ext = caminho.suffix.lower()

    if ext == ".txt":
        texto = caminho.read_text(encoding="utf-8", errors="replace")
        return Documento(caminho, texto, paginas=1, metodo="txt")

    if ext != ".pdf":
        raise ValueError(f"Formato não suportado: {caminho.name}")

    avisos: list[str] = []
    try:
        texto, n = _pdfplumber(caminho)
        metodo = "pdfplumber"
    except Exception as e:  # PDF corrompido / encriptado
        log.warning("pdfplumber falhou em %s: %s", caminho.name, e)
        texto, n, metodo = _pdftotext(caminho), 0, "pdftotext"

    n = max(n, 1)
    if len(texto.strip()) < MIN_CARACTERES_POR_PAGINA * n:
        alt = _pdftotext(caminho)
        if len(alt.strip()) > len(texto.strip()):
            texto, metodo = alt, "pdftotext"

    if len(texto.strip()) < MIN_CARACTERES_POR_PAGINA * n:
        if usar_ocr and ocr_disponivel():
            try:
                texto, metodo = _ocr(caminho), "ocr"
                avisos.append("Texto obtido por OCR — verificar números com atenção.")
            except Exception as e:
                avisos.append(str(e))
        else:
            avisos.append(
                "PDF parece ser digitalizado (sem texto) e o OCR não está instalado "
                "(instalar ocrmypdf + tesseract-ocr-por)."
            )

    return Documento(caminho, texto, paginas=n, metodo=metodo, avisos=avisos)


def listar_documentos(pasta: str | Path) -> list[Path]:
    pasta = Path(pasta)
    return sorted(
        p for p in pasta.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSOES_SUPORTADAS and not p.name.startswith(".")
    )
