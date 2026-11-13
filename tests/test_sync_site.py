import httpx
import pytest

from app.base.config import DATA_DIR
from app.conhecimento import manifest
from app.ingestao import divisao
from app.agentes import grafo as graph
from app.ingestao import sync_site
from app.conhecimento.manifest import Document

HOME = """<html><head><title>x</title><script>js()</script></head><body>
<h1>Intranet</h1><p>Abertura</p>
<h2>Regulamentação</h2><ul><li><a href="dados/concluintes-20261.txt">Concluintes</a></li></ul>
<h2>Temas recorrentes</h2>
<ul><li>Horários de atendimento<ul><li>A secretaria abre às <b>8h</b></li></ul></li>
<li>Aproveitamento<ul><li>Seção IV páginas 15 a 18 do regul. grad. 2007</li><li>Aproveitamento de Inglês: abra processo</li></ul></li></ul>
<h2>Contatos</h2><p>Secretaria: email</p>
</body></html>"""
LONG = "<p>" + "conteúdo da página " * 20 + "</p>"


def page(url="https://cgcc.ufcg.edu.br/trancamentos", arquivo="site-t.md", ingerir=True, **kwargs):
    return Document(arquivo=arquivo, titulo="Título", status="vigente", ingerir=ingerir, url=url, **kwargs)


def fetcher(pages):
    def fetch(url):
        content = pages[url]
        if isinstance(content, Exception):
            raise content
        status = 500 if content == "ERRO" else 200
        return httpx.Response(status, text=content, request=httpx.Request("GET", url))
    return fetch


def test_converte_titulos_listas_e_tabelas():
    html = "<h1>A</h1><h2>B</h2><p>um <b>dois</b></p><ul><li>item</li></ul><table><tr><td>x</td><td>y</td></tr></table>"
    assert sync_site.html_to_marked_text(html) == "# A\n## B\num dois\n- item\nx | y"


def test_remove_scripts_e_cabecalho():
    assert "js()" not in sync_site.html_to_marked_text(HOME)


def test_mantem_so_secoes_listadas():
    text = sync_site.keep_sections(sync_site.html_to_marked_text(HOME), ["Temas recorrentes", "Contatos"])
    assert "Horários de atendimento" in text and "Secretaria: email" in text
    assert "Abertura" not in text and "Concluintes" not in text


def test_remove_referencia_ao_regulamento_antigo():
    document = page(url=sync_site.SITE_URL, arquivo="site-inicial.md", secoes=["Temas recorrentes"])
    text = sync_site.page_to_text(document, HOME)
    assert "2007" not in text
    assert "Aproveitamento de Inglês" in text


def test_cada_tema_da_inicial_vira_secao_e_nao_mistura_assuntos():
    document = page(url=sync_site.SITE_URL, arquivo="site-inicial.md", secoes=["Temas recorrentes", "Contatos"])
    sections = dict(divisao.split_by_heading(sync_site.page_to_text(document, HOME)))
    atendimento = sections["Temas recorrentes — Horários de atendimento"]
    assert "A secretaria abre às 8h" in atendimento
    assert "Inglês" not in atendimento
    assert "Inglês" in sections["Temas recorrentes — Aproveitamento"]
    assert "Secretaria: email" in sections["Contatos"]


def test_titulo_do_tema_nao_engole_o_texto():
    html = "<ul><li><p>Estágios</p><p>Não há estágio obrigatório.</p><ul><li>Página antiga</li></ul></li></ul>"
    assert sync_site.html_to_marked_text(html) == "### Estágios\n- Não há estágio obrigatório.\n- Página antiga"


def test_correcao_sem_efeito_quando_a_pagina_ja_mudou():
    assert sync_site.apply_site_fixes("site-inicial.md", "texto novo") == "texto novo"


def test_mudanca_grava_arquivo_e_repeticao_nao_altera(tmp_path):
    document, state = page(), {}
    fetch = fetcher({document.url: LONG})
    first = sync_site.sync_pages([document], fetch, tmp_path, state)
    assert first.changed == [document.url]
    assert "conteúdo da página" in (tmp_path / "site-t.md").read_text(encoding="utf-8")
    second = sync_site.sync_pages([document], fetch, tmp_path, state)
    assert second.changed == [] and second.unchanged == [document.url]


@pytest.mark.parametrize("content", ["ERRO", "<p>curto</p>", httpx.ConnectError("sem rede")])
def test_falha_mantem_arquivo_anterior_e_segue(tmp_path, content):
    broken, ok = page(), page(url="https://cgcc.ufcg.edu.br/red", arquivo="site-r.md")
    (tmp_path / "site-t.md").write_text("anterior", encoding="utf-8")
    state = {broken.url: "hash-antigo"}
    report = sync_site.sync_pages([broken, ok], fetcher({broken.url: content, ok.url: LONG}), tmp_path, state)
    assert len(report.failed) == 1 and report.changed == [ok.url]
    assert (tmp_path / "site-t.md").read_text(encoding="utf-8") == "anterior"
    assert state[broken.url] == "hash-antigo"


def test_pagina_so_monitorada_pede_revisao_e_nao_grava(tmp_path):
    document, state = page(ingerir=False), {}
    assert sync_site.sync_pages([document], fetcher({document.url: LONG}), tmp_path, state).review == []
    changed = LONG + "<p>nova data</p>"
    report = sync_site.sync_pages([document], fetcher({document.url: changed}), tmp_path, state)
    assert report.review == [document.url]
    assert report.changed == []
    assert not (tmp_path / "site-t.md").exists()


def test_recusa_url_de_concluintes(tmp_path):
    document = page(url="https://cgcc.ufcg.edu.br/dados/concluintes-20261.txt")
    with pytest.raises(ValueError, match="proibida"):
        sync_site.sync_pages([document], fetcher({}), tmp_path, {})


def test_link_novo_e_relatado_sem_repetir_os_ja_avaliados():
    html = """<a href="trancamentos">t</a><a href="novo-tema">n</a><a href="docs/x.pdf">p</a>
    <a href="dados/concluintes-20261.txt">c</a><a href="https://sei.ufcg.edu.br/">e</a><a href="/">i</a>"""
    assert sync_site.find_new_links(html, {"https://cgcc.ufcg.edu.br/trancamentos"}) == ["https://cgcc.ufcg.edu.br/novo-tema"]


def test_manifest_real_nao_tem_url_proibida_e_paginas_antigas_ficam_fora():
    documents = manifest.load_manifest(DATA_DIR / "manifest.yaml")
    urls = {d.url: d for d in documents if d.url}
    assert not any(sync_site.FORBIDDEN_URL_RE.search(url) for url in urls)
    assert not urls["https://cgcc.ufcg.edu.br/revisao"].ingerir
    assert not urls["https://cgcc.ufcg.edu.br/guia-do-tcc"].ingerir


def test_fonte_de_pagina_do_site_mostra_o_link():
    graph.document_titles.cache_clear()
    text = graph.format_sources([{"doc": "site-trancamentos.md", "trecho": "Trancamentos — Tramitação"}])
    assert text == "Fonte: Intranet CGCC — Trancamentos (cgcc.ufcg.edu.br/trancamentos), Trancamentos — Tramitação"
