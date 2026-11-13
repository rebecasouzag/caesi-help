"""Divide o texto dos documentos em trechos, por artigo, título ou página."""
import re
from dataclasses import dataclass

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MIN_CHUNK_CHARS = 20
MIN_ARTICLES = 5  # a partir disso, o documento é dividido por artigo
ARTICLE_RE = re.compile(r"^[ \t]*Art\.?[ \t]*(\d+)", re.MULTILINE)
ANNEX_RE = re.compile(r"^[ \t]*\(?ANEXO[ \t]+([IVX]+)\b", re.MULTILINE)
HIERARCHY_RE = re.compile(r"^[ \t]*(T[IÍ]TULO [IVXLC]+|CAP[IÍ]TULO [IVXLC]+|Se[cç][aã]o [IVXLC]+)[ \t]*\n[ \t]*([^\n]+)", re.MULTILINE)
LEVEL_NAMES = str.maketrans("íçã", "ica")
LEVEL_ORDER = ["titulo", "capitulo", "secao"]
HEADING_RE = re.compile(r"^(#{1,2}) (.+)$", re.MULTILINE)

@dataclass(frozen=True)
class Chunk:
    content: str
    locator: str

def split_by_heading(text: str) -> list[tuple[str, str]]:
    """Separa em (locator, texto). Títulos seguidos do mesmo nível são um só, quebrado em duas linhas."""
    sections = []
    major, minor = None, None
    locator, body = "Início", []
    previous_level = 0
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            body.append(line)
            previous_level = 0
            continue
        level, title = len(match.group(1)), match.group(2)
        if level == previous_level:
            title = f"{(major if level == 1 else minor)} {title}"
            body = []
        else:
            sections.append((locator, "\n".join(body)))
            body = []
        if level == 1:
            major, minor = title, None
        else:
            minor = title
        locator = " — ".join(part for part in (major, minor) if part)
        body = []
        previous_level = level
    sections.append((locator, "\n".join(body)))
    return [(name, f"{name}\n{content}") for name, content in sections if content.strip()]

def subdivide(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Divide textos longos, cortando em espaço em branco para não partir palavras."""
    text = text.strip()
    parts = []
    start = 0
    while len(text) - start > size:
        end = start + size
        cut = max(text.rfind(" ", start + overlap + 1, end), text.rfind("\n", start + overlap + 1, end))
        if cut == -1:
            cut = end
        parts.append(text[start:cut].strip())
        next_start = cut - overlap
        boundary = max(text.rfind(" ", start + 1, next_start), text.rfind("\n", start + 1, next_start))
        start = boundary + 1 if boundary >= 0 else next_start
    parts.append(text[start:].strip())
    return parts

def split_by_article(text: str) -> list[tuple[str, str, str]]:
    """Separa em (locator, texto, contexto). O contexto é o caminho de Título, Capítulo e Seção do artigo.

    Cabeçalhos não ficam no texto do artigo anterior. Um anexo encerra o artigo anterior e o caminho,
    para não herdar o locator nem o contexto dele.
    """
    marks = [(m.start(), m.end(), "artigo", f"Art. {m.group(1)}") for m in ARTICLE_RE.finditer(text)]
    marks += [(m.start(), m.end(), "anexo", f"Anexo {m.group(1)}") for m in ANNEX_RE.finditer(text)]
    marks += [(m.start(), m.end(), "cabecalho", m) for m in HIERARCHY_RE.finditer(text)]
    marks.sort(key=lambda mark: mark[0])
    if not marks:
        return [("Preâmbulo", text, "")]

    sections = [("Preâmbulo", text[: marks[0][0]], "")]
    path = {}
    for (start, end, kind, value), following in zip(marks, marks[1:] + [(len(text), 0, "", "")]):
        if kind == "cabecalho":
            level = value.group(1).split()[0].lower().translate(LEVEL_NAMES)
            for lower in LEVEL_ORDER[LEVEL_ORDER.index(level):]:
                path.pop(lower, None)
            path[level] = f"{value.group(1)} {value.group(2).strip()}"
            continue
        if kind == "anexo":
            path = {}
        context = " > ".join(path[name] for name in LEVEL_ORDER if name in path)
        sections.append((value, text[start : following[0]], context))
    return sections


def build_chunks(pages: list[str]) -> list[Chunk]:
    text = "\n".join(pages)
    if HEADING_RE.search(text):
        sections = split_by_heading(text)
    elif len(ARTICLE_RE.findall(text)) >= MIN_ARTICLES:
        sections = split_by_article(text)
    else:
        sections = [(f"p. {number}", page) for number, page in enumerate(pages, start=1)]
    chunks = []
    for locator, section, *rest in sections:
        context = f"{rest[0]}\n" if rest and rest[0] else ""
        for part in subdivide(section, size=CHUNK_SIZE - len(context)):
            if len(part) >= MIN_CHUNK_CHARS:
                chunks.append(Chunk(context + part, locator))
    return chunks
