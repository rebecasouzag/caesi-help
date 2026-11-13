"""Sincroniza as páginas do site da coordenação listadas no manifest.yaml e reingere a base se algo mudou.

Uso: python -m app.ingestao.sync_site [--sem-ingestao]

Só as páginas com `url` no manifest são baixadas: conteúdo novo nunca entra sozinho, porque alguém do CA precisa
definir o status dele. Links novos na página inicial são apenas relatados.
"""
import hashlib
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from app.base.config import DATA_DIR
from app.conhecimento.manifest import Document, load_document_fixes, load_manifest
from app.ingestao import pipeline

SITE_URL = "https://cgcc.ufcg.edu.br/"
STATE_PATH = DATA_DIR / "site_state.json"
USER_AGENT = "caesi-help/0.1 (assistente do Centro Academico de Computacao da UFCG)"
REQUEST_PAUSE_SECONDS = 1.0
MIN_PAGE_CHARS = 100  # abaixo disso, a página veio quebrada e o arquivo anterior é mantido

# Nunca baixar: listas de concluintes têm dados pessoais de alunos.
FORBIDDEN_URL_RE = re.compile(r"concluintes", re.IGNORECASE)

# Links da página inicial já avaliados e deixados de fora, para o relatório mostrar só o que for novo.
REVIEWED_LINKS_RE = re.compile(
    r"/(site\.css|g|horarios|dados/concluintes-\d+\.txt|imagens/.*|docs/.*\.pdf)$"
)

BLOCK_TAGS = {"p", "ul", "ol", "div", "section", "article", "blockquote", "pre", "dl", "dt", "dd", "table", "thead", "tbody", "br", "hr"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass
class SyncReport:
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)  # páginas monitoradas que mudaram e pedem revisão manual
    new_links: list[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def html_to_marked_text(html: str) -> str:
    """Converte para texto com títulos '# ' e '## ', o formato que a ingestão divide por seção."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "head"]):
        tag.decompose()
    lines: list[str] = []
    buffer: list[str] = []

    def flush(prefix: str = "") -> None:
        text = clean_text("".join(buffer))
        buffer.clear()
        if text:
            lines.append(prefix + text)

    def split_topic(item: Tag) -> tuple[str, list]:
        """Separa o título de um tema (o texto antes do primeiro bloco) do restante do item."""
        children = list(item.children)
        title = []
        for position, child in enumerate(children):
            is_block = isinstance(child, Tag) and child.name in BLOCK_TAGS
            if is_block and clean_text("".join(title)):
                return clean_text("".join(title)), children[position:]
            if is_block and child.name == "p":
                return clean_text(child.get_text()), children[position + 1:]
            if is_block:
                return "", children[position:]
            title.append(child.get_text() if isinstance(child, Tag) else str(child))
        return clean_text("".join(title)), []

    def walk(node: Tag | list, prefix: str = "") -> None:
        for child in node if isinstance(node, list) else node.children:
            if isinstance(child, NavigableString):
                buffer.append(str(child))
            elif not isinstance(child, Tag):
                continue
            elif child.name in HEADING_TAGS:
                flush(prefix)
                title = clean_text(child.get_text())
                if title:
                    lines.append(("# " if child.name == "h1" else "## ") + title)
            elif child.name == "tr":
                flush(prefix)
                cells = [clean_text(cell.get_text()) for cell in child.find_all(["td", "th"])]
                row = " | ".join(cell for cell in cells if cell)
                if row:
                    lines.append(row)
            elif child.name == "li":
                flush(prefix)
                # Item principal com sublista é um tema: vira subseção, para não misturar assuntos no mesmo trecho.
                if not prefix and child.find(["ul", "ol"], recursive=False):
                    title, rest = split_topic(child)
                    if title:
                        lines.append("### " + title)
                    walk(rest, "- ")
                    flush("- ")
                else:
                    walk(child, "- ")
                    flush("- ")
            elif child.name in BLOCK_TAGS:
                flush(prefix)
                walk(child, prefix)
                flush(prefix)
            else:
                walk(child, prefix)

    walk(soup.body or soup)
    flush()
    return "\n".join(lines)


def keep_sections(text: str, sections: list[str]) -> str:
    """Mantém só as seções '## ' listadas."""
    kept, keeping = [], False
    for line in text.splitlines():
        if line.startswith("# "):
            keeping = False
        elif line.startswith("## "):
            keeping = line[3:] in sections
        if keeping:
            kept.append(line)
    return "\n".join(kept)


def apply_site_fixes(filename: str, text: str) -> str:
    for fix in load_document_fixes("site").get(filename, []):
        text = re.sub(fix["padrao"], fix.get("substituir", ""), text, flags=re.DOTALL)
    return text


def page_to_text(document: Document, html: str) -> str:
    text = html_to_marked_text(html)
    if document.secoes:
        # A seção escolhida passa a ser o título principal, e cada tema dela, uma subseção.
        text = re.sub(r"^## ", "# ", keep_sections(text, document.secoes), flags=re.MULTILINE)
    text = re.sub(r"^### ", "## ", text, flags=re.MULTILINE)
    return apply_site_fixes(document.arquivo, text)


def find_new_links(html: str, known_urls: set[str], base_url: str = SITE_URL) -> list[str]:
    host = urlparse(base_url).netloc
    links = {
        urljoin(base_url, tag["href"]).split("#")[0]
        for tag in BeautifulSoup(html, "html.parser").find_all("a", href=True)
    }
    return sorted(
        link for link in links
        if urlparse(link).netloc == host
        and link.rstrip("/") + "/" != base_url
        and link not in known_urls
        and not REVIEWED_LINKS_RE.search(urlparse(link).path)
    )


def load_state(path: pathlib.Path = STATE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_pages(documents: list[Document], fetch, raw_dir: pathlib.Path, state: dict, pause: float = 0.0) -> SyncReport:
    """Baixa cada página. Um erro em uma delas mantém o arquivo anterior e não interrompe as outras."""
    report = SyncReport()
    known_urls = {d.url for d in documents if d.url}
    for document in documents:
        if not document.url:
            continue
        if FORBIDDEN_URL_RE.search(document.url):
            raise ValueError(f"URL proibida no manifest: {document.url}")
        time.sleep(pause)
        try:
            response = fetch(document.url)
            response.raise_for_status()
            html = response.text
            text = page_to_text(document, html)
            if len(text) < MIN_PAGE_CHARS:
                raise ValueError(f"só {len(text)} caracteres de texto")
        except (httpx.HTTPError, ValueError) as exc:
            report.failed.append(f"{document.url}: {exc}")
            continue

        if document.url == SITE_URL:
            report.new_links = find_new_links(html, known_urls)

        digest = fingerprint(text)
        path = raw_dir / document.arquivo
        previous = state.get(document.url)
        unchanged = previous == digest and (not document.ingerir or path.exists())
        state[document.url] = digest
        if unchanged:
            report.unchanged.append(document.url)
        elif not document.ingerir:
            # Página só monitorada: na primeira execução é registrada; depois, pede revisão.
            if previous is not None:
                report.review.append(document.url)
        else:
            path.write_text(text + "\n", encoding="utf-8")
            report.changed.append(document.url)
    return report


def print_report(report: SyncReport) -> None:
    print(f"Sem mudança: {len(report.unchanged)} | Atualizadas: {len(report.changed)} | Falhas: {len(report.failed)}")
    for title, items in (
        ("Atualizadas", report.changed),
        ("Falhas (arquivo anterior mantido)", report.failed),
        ("Páginas fora da base que mudaram: o CA precisa revisar", report.review),
        ("Links novos no site, fora do manifest: o CA precisa definir o status", report.new_links),
    ):
        if items:
            print(f"\n{title}:")
            for item in items:
                print(f"  {item}")


def main() -> int:
    documents = load_manifest(DATA_DIR / "manifest.yaml")
    state = load_state()
    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        report = sync_pages(documents, client.get, DATA_DIR / "raw", state, pause=REQUEST_PAUSE_SECONDS)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print_report(report)
    if report.changed and "--sem-ingestao" not in sys.argv:
        print("\nReingerindo a base...")
        pipeline.main()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
