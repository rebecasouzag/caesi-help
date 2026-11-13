"""Lê PDFs, Markdown e disciplinas e devolve os trechos de cada documento."""
import json
import pathlib
import re
from collections import Counter

import pymupdf

from app.conhecimento.manifest import Document, load_document_fixes
from app.ingestao.divisao import Chunk, build_chunks, subdivide

MIN_CHARS_PER_PAGE = 100  # abaixo disso, o PDF provavelmente é escaneado
REPEATED_LINE_MIN_PAGES = 4
SECTIONED_DOCS = {"dados-orientacoes-computacao.pdf"}  # títulos marcados pela fonte; dividir por seção, não por página

def normalize(text: str) -> str:
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" ?\n ?", "\n", text).strip()

def remove_repeated_lines(pages: list[str]) -> list[str]:
    """Remove cabeçalhos e rodapés: linhas presentes em pelo menos metade das páginas."""
    if len(pages) < REPEATED_LINE_MIN_PAGES:
        return pages
    seen = Counter(line for page in pages for line in set(page.splitlines()) if line.strip())
    repeated = {line for line, n in seen.items() if n >= len(pages) / 2}
    return ["\n".join(l for l in page.splitlines() if l not in repeated) for page in pages]

def apply_cleanups(filename: str, pages: list[str]) -> list[str]:
    for fix in load_document_fixes("pdf").get(filename, []):
        pattern = re.compile(fix["padrao"], re.DOTALL)
        if not any(pattern.search(page) for page in pages):
            raise ValueError(f"Limpeza não encontrada em {filename}: {fix['padrao']}")
        pages = [pattern.sub(fix.get("substituir", ""), page) for page in pages]
    return pages

def heading_level(size: float, font: str) -> int:
    """Nível do título conforme a fonte do documento compilado: 1 = seção, 2 = subseção, 0 = texto comum."""
    if size >= 18:
        return 1
    if size >= 14 or (font == "Roboto-Regular" and round(size) == 11):
        return 2
    return 0

def marked_text(page) -> str:
    """Extrai o texto marcando os títulos com '# ' e '## '."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                main = max(line["spans"], key=lambda span: len(span["text"]))
                level = heading_level(main["size"], main["font"])
                lines.append(f"{'#' * level} {text}" if level else text)
    return "\n".join(lines)

def read_pages(path: pathlib.Path) -> list[str]:
    if path.suffix == ".md":
        return [normalize(path.read_text(encoding="utf-8"))]
    with pymupdf.open(path) as pdf:
        pages = [normalize(marked_text(page) if path.name in SECTIONED_DOCS else page.get_text()) for page in pdf]
    if sum(len(p) for p in pages) < MIN_CHARS_PER_PAGE * len(pages):
        raise ValueError(f"{path.name} rendeu pouco texto; pode ser um PDF escaneado.")
    return pages

def discipline_chunks(path: pathlib.Path) -> list[Chunk]:
    """Nome e ementa, para achar a disciplina por assunto. Requisitos e horário vêm do catálogo, com as correções do CA."""
    records = {r["nome"]: r for r in json.loads(path.read_text(encoding="utf-8"))}  # o arquivo repete uma disciplina
    chunks = []
    for name, record in records.items():
        chunks += [Chunk(part, name) for part in subdivide(f"Disciplina: {name}\nEmenta: {record['ementa']}")]
    return chunks


def extract_chunks(document: Document, raw_dir: pathlib.Path) -> list[Chunk]:
    if document.arquivo.endswith(".json"):
        return discipline_chunks(raw_dir / document.arquivo)
    pages = apply_cleanups(document.arquivo, read_pages(raw_dir / document.arquivo))
    chunks = build_chunks(remove_repeated_lines(pages))
    return [c for c in chunks if not c.locator.startswith(tuple(document.excluir_secoes))] if document.excluir_secoes else chunks
