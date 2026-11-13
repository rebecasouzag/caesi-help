import datetime as dt
import pathlib

import numpy as np
import pytest
from pydantic import ValidationError

from app.conhecimento import manifest
from app.ingestao import divisao, extratores, pipeline
from app.conhecimento.manifest import Document, FaqEntry

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"


def document(arquivo="a.pdf", ingerir=True, **kwargs):
    return Document(arquivo=arquivo, titulo="Título", status="vigente", ingerir=ingerir, **kwargs)


def faq_entry(faq_id="x", **kwargs):
    defaults = dict(id=faq_id, categoria="c", pergunta="p?", resposta="r", curriculo="todos", status="rascunho")
    return FaqEntry(**{**defaults, **kwargs})


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table, self.action, self.payload, self.filters = client, table, "select", None, []

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, rows):
        self.action, self.payload = "insert", rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, row):
        self.action, self.payload = "upsert", row
        return self

    def delete(self):
        self.action = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(lambda r: r[column] == value)
        return self

    def in_(self, column, values):
        self.filters.append(lambda r: r[column] in values)
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        if self.action == "insert":
            rows.extend(self.payload)
        elif self.action == "upsert":
            rows[:] = [r for r in rows if r["id"] != self.payload["id"]] + [self.payload]
        elif self.action == "delete":
            rows[:] = [r for r in rows if not all(f(r) for f in self.filters)]
        return type("Response", (), {"data": list(rows)})


class FakeClient:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return FakeQuery(self, name)


def test_manifest_real_e_valido_e_arquivos_existem():
    documents = manifest.load_manifest(DATA_DIR / "manifest.yaml")
    faq = manifest.load_all_faq(DATA_DIR)
    manifest.validate_sources(documents, faq, DATA_DIR)
    assert sum(d.ingerir and not d.url for d in documents) == 10
    assert len(faq) == 62


def test_status_invalido_e_rejeitado():
    with pytest.raises(ValidationError):
        Document(arquivo="a.pdf", titulo="t", status="vencido", ingerir=True)
    with pytest.raises(ValidationError):
        faq_entry(curriculo="2020")


def test_depende_exige_texto_dos_dois_curriculos():
    with pytest.raises(ValidationError, match="2017 e 2023"):
        faq_entry(curriculo="depende", respostas_por_curriculo={"2023": "texto"})


def test_divide_por_titulo_com_secao_e_subsecao():
    text = "# Trancamentos\nIntrodução do tema.\n## Tramitação\nPasso a passo.\n# Abono de\n# Faltas\n## Tramitação\nOutro passo."
    assert divisao.split_by_heading(text) == [
        ("Trancamentos", "Trancamentos\nIntrodução do tema."),
        ("Trancamentos — Tramitação", "Trancamentos — Tramitação\nPasso a passo."),
        ("Abono de Faltas — Tramitação", "Abono de Faltas — Tramitação\nOutro passo."),
    ]


def test_dados_orientacoes_real_tem_gestao_em_secao_propria():
    chunks = extratores.extract_chunks(document("dados-orientacoes-computacao.pdf"), RAW_DIR)
    gestao = [c for c in chunks if c.locator.endswith("Coordenação Executiva Colegiada")]
    assert gestao and "Prof. Thiago Emmanuel" in gestao[0].content
    assert not any("# " in c.content for c in chunks)


def test_disciplinas_na_busca_tem_so_nome_e_ementa():
    chunks = extratores.extract_chunks(document("disciplinas.json"), RAW_DIR)
    assert len({c.locator for c in chunks}) == 90
    first = next(c for c in chunks if c.locator == "Programação II")
    assert first.content.startswith("Disciplina: Programação II\nEmenta:")
    assert not any("Bibliografia" in c.content or "Pré-requisitos:" in c.content for c in chunks)


def test_compilado_nao_repete_o_que_as_paginas_do_site_cobrem():
    real = next(d for d in manifest.load_manifest(DATA_DIR / "manifest.yaml") if d.arquivo == "dados-orientacoes-computacao.pdf")
    locators = {c.locator for c in extratores.extract_chunks(real, RAW_DIR)}
    assert not {l for l in locators if l.startswith(("Trancamentos", "Abono,", "Antecipação de Estudos", "Colegiado", "NDE"))}
    assert "Atividades complementares e de extensão — Como pedir?" in locators
    assert not {l for l in locators if l.startswith("Gestão da Unidade Acadêmica — Como")}


def test_fonte_inexistente_interrompe(tmp_path):
    (tmp_path / "raw").mkdir()
    entry = faq_entry(fontes=[{"doc": "nao-existe.pdf", "trecho": "Art. 1"}])
    with pytest.raises(FileNotFoundError, match="nao-existe.pdf"):
        manifest.validate_sources([], [entry], tmp_path)


def test_divide_por_artigo_com_formatos_variados():
    text = "Preâmbulo da resolução.\n" + "\n".join(
        f"{prefix} texto do artigo com conteúdo suficiente."
        for prefix in ["Art. 1º", "Art. 2°", "Art 3o", "Art. 4", "Art. 5."]
    )
    chunks = divisao.build_chunks([text])
    assert [c.locator for c in chunks] == ["Preâmbulo", "Art. 1", "Art. 2", "Art. 3", "Art. 4", "Art. 5"]


def test_anexo_nao_herda_locator_do_ultimo_artigo():
    articles = "\n".join(f"Art. {i}. Conteúdo suficiente do artigo." for i in range(1, 6))
    chunks = divisao.build_chunks([articles + "\n(ANEXO II DA RESOLUÇÃO)\nCálculo dos indicadores."])
    assert chunks[-1].locator == "Anexo II"
    assert "Cálculo" not in chunks[-2].content


def test_sem_artigos_divide_por_pagina():
    chunks = divisao.build_chunks(["Conteúdo da primeira página.", "Conteúdo da segunda página."])
    assert [c.locator for c in chunks] == ["p. 1", "p. 2"]


def test_trecho_longo_mantem_locator_e_sobreposicao():
    words = " ".join(f"palavra{i}" for i in range(800))
    chunks = divisao.build_chunks([f"Art. {i}. curto o bastante aqui." for i in range(1, 5)] + [f"Art. 5. {words}"])
    long_parts = [c for c in chunks if c.locator == "Art. 5"]
    assert len(long_parts) > 1
    assert all(len(c.content) <= divisao.CHUNK_SIZE for c in long_parts)
    assert long_parts[0].content.split()[-1] in long_parts[1].content.split()


def test_subdivide_nao_parte_palavras():
    words = [f"palavra{i}" for i in range(800)]
    parts = divisao.subdivide(" ".join(words))
    assert {w for part in parts for w in part.split()} == set(words)


@pytest.mark.parametrize("text", [
    "x" * 5000,
    ("a " * 200) + ("y" * 1000) + (" b" * 200),
    ("a " * 80) + ("y" * 300) + (" " + "z" * 300) * 5,
])
def test_subdivide_termina_com_palavras_longas(text):
    parts = divisao.subdivide(text)
    assert all(len(p) <= divisao.CHUNK_SIZE for p in parts)
    assert parts[-1].endswith(text[-1])


def test_remove_linhas_repetidas():
    pages = [f"Rodapé oficial\nConteúdo {i}" for i in range(4)]
    assert extratores.remove_repeated_lines(pages) == [f"Conteúdo {i}" for i in range(4)]


def test_limpeza_nao_encontrada_interrompe():
    with pytest.raises(ValueError, match="Limpeza não encontrada"):
        extratores.apply_cleanups("guia-do-tcc-26.1.pdf", ["texto sem calendário"])


def test_guia_do_tcc_real_sem_datas():
    chunks = extratores.extract_chunks(document("guia-do-tcc-26.1.pdf"), RAW_DIR)
    content = "\n".join(c.content for c in chunks)
    assert "2026" not in content
    assert "Calendário" not in content
    assert "Acordo de orientação" in content
    assert "Consolidação do resultado" in content


def test_dados_orientacoes_real_limpo():
    content = "\n".join(c.content for c in extratores.extract_chunks(document("dados-orientacoes-computacao.pdf"), RAW_DIR))
    assert "Profa. Thiago" not in content
    assert "Prof. Thiago Emmanuel" in content
    assert "Coordenadora Administrativa" not in content
    assert "Coordenador Administrativo" in content
    assert "ativos@ccc" not in content
    assert "Caros alunos" in content


def test_regulamento_real_cita_artigos_sem_rodape():
    chunks = extratores.extract_chunks(document("res_11_2024-cse.pdf"), RAW_DIR)
    assert {"Art. 270", "Art. 382", "Anexo II"} <= {c.locator for c in chunks}
    assert not any("Boletim de Serviço Eletrônica" in c.content for c in chunks)


def test_embed_usa_prefixo_passage_e_valida_dimensao():
    class Model:
        def encode(self, texts, **kwargs):
            self.texts, self.kwargs = texts, kwargs
            return np.zeros((len(texts), 384))

    model = Model()
    assert len(pipeline.embed(model, ["trecho"], 384)[0]) == 384
    assert model.texts == ["passage: trecho"]
    assert model.kwargs["normalize_embeddings"] is True
    with pytest.raises(ValueError, match="EMBEDDING_DIM"):
        pipeline.embed(model, ["trecho"], 768)


def test_sync_documents_ignora_nao_ingerir_e_nao_duplica():
    client = FakeClient(documents=[], chunks=[])
    documents = [document("a.pdf", atualizado_em=dt.date(2025, 1, 2)), document("b.pdf", ingerir=False)]
    rows = {"a.pdf": [{"document_id": "a.pdf", "content": "c", "locator": "p. 1", "embedding": [0.0]}]}
    for _ in range(2):
        pipeline.sync_documents(client, documents, rows)
    assert [d["id"] for d in client.tables["documents"]] == ["a.pdf"]
    assert client.tables["documents"][0]["updated_at"] == "2025-01-02"
    assert len(client.tables["chunks"]) == 1


def test_sync_documents_remove_documento_que_saiu_da_base():
    client = FakeClient(documents=[{"id": "b.pdf"}], chunks=[])
    pipeline.sync_documents(client, [document("b.pdf", ingerir=False)], {})
    assert client.tables["documents"] == []


def test_sync_faq_preserva_escalonamento_e_remove_yaml_antigo():
    client = FakeClient(
        faq=[{"id": "promovida", "origin": "escalonamento"}, {"id": "antiga", "origin": "yaml"}],
        faq_variations=[],
    )
    entry = faq_entry("nova", variacoes=["v1", "v2"])
    for _ in range(2):
        pipeline.sync_faq(client, [entry], {"nova": [0.1]}, {"nova": [[0.2], [0.3]]})
    assert {row["id"] for row in client.tables["faq"]} == {"promovida", "nova"}
    assert [v["text"] for v in client.tables["faq_variations"]] == ["v1", "v2"]


def test_sync_faq_recusa_sobrescrever_entrada_promovida():
    client = FakeClient(faq=[{"id": "x", "origin": "escalonamento"}], faq_variations=[])
    with pytest.raises(ValueError, match="promovidas"):
        pipeline.sync_faq(client, [faq_entry("x")], {"x": [0.1]}, {"x": []})
    assert client.tables["faq"] == [{"id": "x", "origin": "escalonamento"}]


REGULATION = """Preâmbulo da resolução, com texto suficiente.
TÍTULO I
DAS DISPOSIÇÕES GERAIS
CAPÍTULO I
DOS CONCEITOS
Art. 1º Primeiro artigo com texto suficiente para virar trecho.
Art. 2º Segundo artigo com texto suficiente para virar trecho.
CAPÍTULO II
DAS MATRÍCULAS
Seção I
Do Ajuste
Art. 3º Terceiro artigo com texto suficiente para virar trecho.
TÍTULO II
DA AVALIAÇÃO
Art. 4º Quarto artigo com texto suficiente para virar trecho.
Art. 5º Quinto artigo com texto suficiente para virar trecho.
ANEXO I
Texto do anexo com tamanho suficiente para virar trecho.
"""


def regulation_chunks():
    return {c.locator: c.content for c in divisao.build_chunks([REGULATION])}


def test_artigo_carrega_o_caminho_de_titulo_capitulo_e_secao():
    chunks = regulation_chunks()
    assert chunks["Art. 1"].startswith("TÍTULO I DAS DISPOSIÇÕES GERAIS > CAPÍTULO I DOS CONCEITOS\nArt. 1º")
    assert chunks["Art. 3"].startswith("TÍTULO I DAS DISPOSIÇÕES GERAIS > CAPÍTULO II DAS MATRÍCULAS > Seção I Do Ajuste\nArt. 3º")


def test_novo_titulo_limpa_capitulo_e_secao_anteriores():
    assert regulation_chunks()["Art. 4"].startswith("TÍTULO II DA AVALIAÇÃO\nArt. 4º")


def test_cabecalho_nao_gruda_no_artigo_anterior():
    assert "CAPÍTULO II" not in regulation_chunks()["Art. 2"].split("\n", 1)[1]
    assert "TÍTULO II" not in regulation_chunks()["Art. 3"].split("\n", 1)[1]


def test_anexo_nao_herda_o_contexto_do_artigo():
    assert regulation_chunks()["Anexo I"].startswith("ANEXO I")


def test_artigo_real_166_traz_seu_contexto_e_respeita_o_tamanho():
    chunks = extratores.extract_chunks(document("res_11_2024-cse.pdf"), RAW_DIR)
    article = next(c for c in chunks if c.locator == "Art. 166")
    assert "TÍTULO" in article.content.split("\n")[0]
    assert all(len(c.content) <= divisao.CHUNK_SIZE for c in chunks)
