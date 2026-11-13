import httpx
import pytest

from app.ferramentas import academico, calendario, normas
from app.ferramentas.evidencia import Evidencia

CALENDARS = [
    {"id": 1, "periodo": "2025.2", "inicio_das_matriculas": "2025-10-04", "inicio_das_aulas": "2025-10-16",
     "um_quarto_do_periodo": "2025-11-23", "um_terco_do_periodo": "2025-12-06", "ultimo_dia_para_registro_de_notas": "2026-03-28"},
    {"id": 2, "periodo": "2027.1", "inicio_das_matriculas": "2027-03-01", "inicio_das_aulas": "2027-03-08",
     "um_quarto_do_periodo": "2027-04-20", "um_terco_do_periodo": "2027-05-01", "ultimo_dia_para_registro_de_notas": "2027-08-01"},
]


@pytest.fixture(autouse=True)
def clean_cache():
    calendario.reset_cache()
    yield
    calendario.reset_cache()


def test_evidencia_vira_trecho_do_grafo_atual():
    chunk = Evidencia("x", "texto", "doc.pdf", "Título", "Art. 1").as_chunk()
    assert chunk["document_id"] == "doc.pdf" and chunk["locator"] == "Art. 1" and chunk["content"] == "texto"


def test_buscar_disciplina_traz_dados_e_horario():
    result = academico.buscar_disciplina("psoft")
    assert result.status == "encontrado"
    facts, schedule = result.evidencias
    assert "Pré-requisitos: Estruturas de Dados e Algoritmos" in facts.texto
    assert "Giovanni Farias da Silva" in schedule.texto


def test_buscar_disciplina_distingue_ambiguo_de_nao_encontrado():
    ambiguous = academico.buscar_disciplina("banco")
    assert ambiguous.status == "ambiguo" and "Banco de Dados I" in ambiguous.opcoes
    assert academico.buscar_disciplina("zzzz").status == "nao_encontrado"


def test_buscar_pessoa_traz_funcao_contato_e_aulas():
    result = academico.buscar_pessoa("thiago emanuel")
    assert result.status == "encontrado"
    assert "Coordenador Administrativo" in result.evidencias[0].texto
    assert "Aulas em" in result.evidencias[1].texto


def test_horario_de_aceita_disciplina_ou_pessoa():
    assert "Turma 01" in academico.horario_de("eda").evidencias[0].texto
    assert "Docente: Rohit Gheyi" in academico.horario_de("rohit").evidencias[0].texto
    assert academico.horario_de("zzzz").status == "nao_encontrado"


def test_relacao_calcula_pre_requisito_e_traz_a_regra(monkeypatch):
    rule = Evidencia("r", "Art. 147...", "res_11_2024-cse.pdf", "Regulamento", "Art. 147")
    monkeypatch.setattr(academico, "ler_artigo", lambda doc, art: normas.Resultado("encontrado", [rule]))
    result = academico.relacao_entre_disciplinas(["es", "analise de sistemas"])
    assert "Engenharia de Software é pré-requisito direto de Análise de Sistemas" in result.evidencias[0].texto
    assert result.evidencias[1].trecho == "Art. 147"


def test_relacao_precisa_de_duas_disciplinas():
    assert academico.relacao_entre_disciplinas(["eda"]).status == "nao_encontrado"
    assert academico.relacao_entre_disciplinas(["banco", "eda"]).status == "ambiguo"


def test_buscar_faq_nao_entrega_rascunho_nem_vencida(monkeypatch):
    entry = {"id": "a", "question": "q", "answer": "r", "status": "revisado", "sources": [{"doc": "d.pdf", "trecho": "Art. 1"}]}
    monkeypatch.setattr(normas, "search_faq", lambda p, k: [
        entry,
        {**entry, "id": "b", "status": "rascunho"},
        {**entry, "id": "c", "valid_until": "2020-01-01"},
        {**entry, "id": "d", "provenance": "comunidade", "status": "confirmar", "valid_until": "2999-01-01"},
    ])
    result = normas.buscar_faq("?")
    assert [e.id for e in result.evidencias] == ["faq:a", "faq:d"]
    assert result.evidencias[1].procedencia == "comunidade"


def test_buscar_faq_sem_candidatas(monkeypatch):
    monkeypatch.setattr(normas, "search_faq", lambda p, k: [])
    assert normas.buscar_faq("?").status == "nao_encontrado"


def test_calendario_formata_as_datas_do_periodo_atual():
    result = calendario.calendario_atual(fetch=lambda: CALENDARS)
    text = result.evidencias[0].texto
    assert result.status == "encontrado"
    assert "Período 2027.1" in text and "08/03/2027" in text and "01/08/2027" in text
    assert result.evidencias[0].url == calendario.OFFICIAL_CALENDAR_URL


def test_calendario_usa_cache_de_24h():
    calls = []

    def fetch():
        calls.append(1)
        return CALENDARS

    calendario.calendario_atual(fetch=fetch, now=lambda: 1000)
    calendario.calendario_atual(fetch=fetch, now=lambda: 1000 + calendario.CACHE_SECONDS - 1)
    assert len(calls) == 1
    calendario.calendario_atual(fetch=fetch, now=lambda: 1000 + calendario.CACHE_SECONDS + 1)
    assert len(calls) == 2


def test_calendario_com_periodo_repetido_e_sem_confirmacao_nao_afirma_data(monkeypatch):
    repeated = [{**CALENDARS[1], "id": 7}, {**CALENDARS[1], "id": 8, "inicio_das_aulas": "2027-04-01"}]
    result = calendario.calendario_atual(fetch=lambda: repeated)
    assert result.status == "indisponivel"
    assert result.evidencias == []
    assert calendario.OFFICIAL_CALENDAR_URL in result.mensagem


def test_periodo_2026_2_real_usa_o_calendario_confirmado_pelo_ca():
    result = calendario.calendario_atual(fetch=lambda: [
        {**CALENDARS[1], "id": 11426251, "periodo": "2026.2", "inicio_das_aulas": "2026-09-08"},
        {**CALENDARS[1], "id": 11462353, "periodo": "2026.2", "inicio_das_aulas": "2026-10-09"},
    ])
    assert "08/09/2026" in result.evidencias[0].texto


def test_api_fora_do_ar_usa_a_copia_e_avisa(monkeypatch, tmp_path):
    def fails():
        raise httpx.ConnectError("sem rede")

    snapshot = tmp_path / "calendarios.json"
    snapshot.write_text('[{"id": 2, "periodo": "2027.1", "inicio_das_matriculas": "2027-03-01", "inicio_das_aulas": "2027-03-08", '
                        '"um_quarto_do_periodo": "2027-04-20", "um_terco_do_periodo": "2027-05-01", '
                        '"ultimo_dia_para_registro_de_notas": "2027-08-01"}]', encoding="utf-8")
    monkeypatch.setattr(calendario, "SNAPSHOT_PATH", snapshot)
    result = calendario.calendario_atual(fetch=fails)
    assert "podem estar desatualizadas" in result.evidencias[0].texto


def test_api_fora_do_ar_sem_copia_fica_indisponivel(monkeypatch, tmp_path):
    def fails():
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(calendario, "SNAPSHOT_PATH", tmp_path / "nao-existe.json")
    assert calendario.calendario_atual(fetch=fails).status == "indisponivel"


@pytest.mark.parametrize("code, expected", [
    ("24T23", "segunda e quarta, das 14h às 16h"),
    ("35M45", "terça e quinta, das 10h às 12h"),
    ("6M23", "sexta, das 8h às 10h"),
    ("246T45", "segunda, quarta e sexta, das 16h às 18h"),
    ("o que significa 24t23 no sigaa?", "segunda e quarta, das 14h às 16h"),
])
def test_decodifica_horario_do_sigaa(code, expected):
    result = calendario.decodificar_horario_sigaa(code)
    assert expected in result.evidencias[0].texto
    assert result.evidencias[0].procedencia == "comunidade"


@pytest.mark.parametrize("text", ["sem código aqui", "24N23", "24T25", "89T23", "res 01/2023"])
def test_codigo_invalido_ou_noturno_nao_e_interpretado(text):
    assert calendario.decodificar_horario_sigaa(text).status == "nao_encontrado"
