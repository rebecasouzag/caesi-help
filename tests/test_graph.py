import pytest

from app.conhecimento import requisitos
from app.agentes import grafo as graph
from app.canais import cli
from app.agentes.grafo import Classification, Draft, FaqChoice, Verification
from app.ferramentas.contatos import search_contacts
from app.ferramentas.evidencia import Evidencia, Resultado

FAQ_HIT = {
    "id": "estagio-requisitos", "category": "estagio", "question": "Quando posso estagiar?",
    "answer": "Com 80 créditos.", "curriculum": "todos", "status": "revisado", "similarity": 0.95,
    "sources": [{"doc": "resolucao-estagios-01-2020.pdf", "trecho": "Art. 3º"}],
}
CHUNK = {
    "id": 1, "document_id": "RES_09.2025_-_MONITORIA.pdf", "title": "Resolução CSE 09/2025 — Programa de Monitoria",
    "status": "vigente", "content": "O monitor cumpre 12 horas semanais.", "locator": "Art. 25", "similarity": 0.86,
}


class Recorder:
    """Substitui o Supabase: guarda o que seria gravado em conversations e escalations."""

    def __init__(self):
        self.rows = {"conversations": [], "escalations": []}

    def table(self, name):
        recorder = self

        class Query:
            def insert(self, row):
                self.row = {**row, "id": f"{name}-{len(recorder.rows[name]) + 1}"}
                recorder.rows[name].append(self.row)
                return self

            def execute(self):
                return type("Response", (), {"data": [self.row]})

        return Query()


@pytest.fixture
def agent(monkeypatch):
    """Cenário padrão: pergunta sobre o curso, sem resultados. Cada teste altera o que precisar."""
    recorder = Recorder()
    env = type("Env", (), {})()
    env.db = recorder
    env.replies = {
        Classification: Classification(intent="faq_docs", categoria="estagio"),
        FaqChoice: FaqChoice(faq_id=None),
        Draft: Draft(sufficient=False),
        Verification: Verification(supported=True, answers_question=True),
    }
    env.faq = []
    env.docs = []
    monkeypatch.setattr(graph, "ask", lambda schema, system, user: env.replies[schema])
    monkeypatch.setattr(graph, "search_faq", lambda question: env.faq)
    monkeypatch.setattr(graph, "search_docs", lambda question: env.docs)
    monkeypatch.setattr(graph, "get_client", lambda: recorder)
    return env


def test_faq_validada_devolve_texto_revisado_com_fonte(agent):
    agent.faq = [FAQ_HIT]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    result = graph.answer_question("posso estagiar?")
    assert result["route"] == "faq"
    assert result["answer"].startswith("Com 80 créditos.")
    assert "Fonte: Resolução 01/2020 do Colegiado — Regulamento de Estágio, Art. 3º" in result["answer"]
    assert agent.db.rows["escalations"] == []
    assert len(agent.db.rows["conversations"]) == 1


def test_nota_alta_sem_validacao_nao_responde_pelo_faq(agent):
    agent.faq = [FAQ_HIT]
    result = graph.answer_question("como consigo a carteirinha?")
    assert result["route"] == "escalated"
    assert len(agent.db.rows["escalations"]) == 1


def test_id_inventado_pelo_llm_nao_e_aceito(agent):
    agent.faq = [FAQ_HIT]
    agent.replies[FaqChoice] = FaqChoice(faq_id="nao-existe")
    assert graph.answer_question("pergunta")["route"] == "escalated"


def test_candidatos_abaixo_do_limiar_nao_chegam_ao_llm(agent):
    agent.faq = [{**FAQ_HIT, "similarity": graph.LIMIAR_FAQ - 0.01}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert graph.answer_question("pergunta")["route"] == "escalated"


def test_status_confirmar_leva_aviso(agent):
    agent.faq = [{**FAQ_HIT, "status": "confirmar"}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert "confirme com a coordenação" in graph.answer_question("pergunta")["answer"]


def test_rascunho_nunca_chega_ao_llm_nem_ao_aluno(agent):
    agent.faq = [{**FAQ_HIT, "status": "rascunho"}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert graph.answer_question("pergunta")["route"] == "escalated"


def test_curriculo_depende_responde_as_duas_opcoes_sem_perguntar(agent):
    entry = {**FAQ_HIT, "answer": "2017: 22 créditos. 2023: 8 créditos.", "curriculum": "depende",
             "answer_by_curriculum": {"2017": "Texto de 2017.", "2023": "Texto de 2023."}}
    agent.faq = [entry]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    result = graph.answer_question("quantos créditos preciso?")
    assert result["route"] == "faq"
    assert result["answer"].startswith("2017: 22 créditos. 2023: 8 créditos.")
    assert len(agent.db.rows["conversations"]) == 1


@pytest.mark.parametrize("text, expected", [
    ("quantos créditos no currículo 2023?", "2023"),
    ("estou na grade 2017", "2017"),
    ("sou do 2017 curriculo", "2017"),
    ("a Res. 01/2023 fala o que sobre extensão?", None),
    ("entrei em 2023", None),
])
def test_ano_so_vale_como_curriculo_quando_citado_junto(text, expected):
    assert graph.detect_curriculum(text) == expected


def test_depende_devolve_so_o_texto_do_curriculo_informado(agent):
    by_curriculum = {"2017": "Texto de 2017.", "2023": "Texto de 2023."}
    agent.faq = [{**FAQ_HIT, "curriculum": "depende", "answer_by_curriculum": by_curriculum}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    answer = graph.answer_question("quantos créditos preciso?", curriculum="2023")["answer"]
    assert answer.startswith("Texto de 2023.")
    assert "2017" not in answer


def test_entrada_de_outro_curriculo_e_descartada(agent):
    agent.faq = [{**FAQ_HIT, "curriculum": "2023"}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert graph.answer_question("pergunta", curriculum="2017")["route"] == "escalated"


def test_entrada_de_um_curriculo_avisa_qual_e(agent):
    agent.faq = [{**FAQ_HIT, "curriculum": "2023"}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert graph.answer_question("pergunta")["answer"].startswith("Vale para o currículo 2023.")


def test_documentos_respondem_com_titulo_e_artigo(agent):
    agent.docs = [CHUNK]
    agent.replies[Draft] = Draft(sufficient=True, answer="São 12 horas semanais.", used=[1])
    result = graph.answer_question("quantas horas?")
    assert result["route"] == "docs"
    assert result["answer"].endswith("Fonte: Resolução CSE 09/2025 — Programa de Monitoria, Art. 25")


def test_resposta_com_disciplinas_leva_aviso_antes_da_fonte(agent):
    agent.docs = [{**CHUNK, "document_id": "disciplinas.json", "locator": "Programação II"}]
    agent.replies[Draft] = Draft(sufficient=True, answer="O pré-requisito é Programação I.", used=[1])
    answer = graph.answer_question("qual o pré-requisito de programação 2?")["answer"]
    notice = "Esses são os dados do PPC 2023, mas pode haver mudanças feitas diretamente no SIGAA sobre os pré-requisitos."
    assert answer.index(notice) < answer.index("Fonte: Disciplinas do curso (PPC 2023), Programação II")


def test_pergunta_sobre_disciplinas_pula_o_faq_e_usa_os_dados_delas(agent, monkeypatch):
    mentioned = []
    relation = {**CHUNK, "id": -1, "document_id": "disciplinas.json", "locator": "Relação entre A, B"}
    monkeypatch.setattr(graph, "discipline_context", lambda names: mentioned.extend(names) or [relation])
    agent.replies[Classification] = Classification(intent="faq_docs", categoria="matricula", disciplinas=["A", "B"])
    agent.faq = [FAQ_HIT]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    agent.replies[Draft] = Draft(sufficient=True, answer="Pode, se tiver os pré-requisitos.", used=[1])
    agent.docs = [{**CHUNK, "id": n} for n in range(2, 12)]
    result = graph.answer_question("posso pagar A e B?")
    assert mentioned == ["A", "B"]
    assert result["route"] == "docs"
    assert "Relação entre A, B" in result["answer"]


def test_disciplina_que_o_classificador_esqueceu_ainda_consulta_o_catalogo(agent, monkeypatch):
    mentioned = []
    facts = {**CHUNK, "id": "x", "document_id": "eureca-horarios", "locator": "Horário de Projeto de Software"}
    monkeypatch.setattr(graph, "discipline_context", lambda names: mentioned.extend(names) or [facts])
    agent.replies[Draft] = Draft(sufficient=True, answer="Fábio Jorge Almeida Morais.", used=[1])
    assert graph.answer_question("quem da aula de psoft?")["route"] == "docs"
    assert mentioned == ["psoft"]


def test_disciplina_reconhecida_no_texto_nao_pula_o_faq(agent, monkeypatch):
    monkeypatch.setattr(graph, "discipline_context", lambda names: [])
    agent.faq = [FAQ_HIT]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    assert graph.answer_question("como tranco eda?")["route"] == "faq"


CALENDAR = Evidencia("c", "Início das aulas: 08/09/2026.", "eureca-calendario", "Calendário acadêmico no Eureca", "Período 2026.2")


def test_pergunta_de_data_sem_faq_usa_o_calendario(agent, monkeypatch):
    monkeypatch.setattr(graph, "calendario_atual", lambda: Resultado("encontrado", [CALENDAR]))
    agent.replies[Classification] = Classification(intent="eureca", calendario=True)
    agent.replies[Draft] = Draft(sufficient=True, answer="Em 08/09/2026.", used=[1])
    result = graph.answer_question("quando começaram as aulas?")
    assert result["route"] == "docs"
    assert result["answer"].endswith("Fonte: Calendário acadêmico no Eureca, Período 2026.2")


def test_pergunta_de_prazo_mantem_o_aviso_do_faq_e_acrescenta_as_datas(agent, monkeypatch):
    """Caso real: o FAQ avisa que há duas regras de prazo; o calendário não pode apagar esse aviso."""
    monkeypatch.setattr(graph, "calendario_atual", lambda: Resultado("encontrado", [CALENDAR]))
    agent.replies[Classification] = Classification(intent="faq_docs", calendario=True)
    agent.faq = [{**FAQ_HIT, "status": "confirmar", "answer": "Há duas referências de prazo."}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    answer = graph.answer_question("até quando posso trancar?")["answer"]
    assert answer.startswith("Há duas referências de prazo.")
    assert "08/09/2026" in answer
    assert graph.CONFIRM_NOTICE in answer
    assert "Calendário acadêmico no Eureca" in answer


def test_em_datas_o_relato_de_colegas_nao_e_candidato(agent, monkeypatch):
    monkeypatch.setattr(graph, "calendario_atual", lambda: Resultado("encontrado", [CALENDAR]))
    agent.replies[Classification] = Classification(intent="eureca", calendario=True)
    agent.faq = [{**FAQ_HIT, "provenance": "comunidade", "status": "confirmar"}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="estagio-requisitos")
    agent.replies[Draft] = Draft(sufficient=True, answer="Em 08/09/2026.", used=[1])
    assert graph.answer_question("quando acaba o período?")["route"] == "docs"


@pytest.mark.parametrize("draft", ["São correquisitos [5]. Cursar junto [1][2].", "São correquisitos【5】. Cursar junto【1】."])
def test_numeros_dos_trechos_nao_chegam_ao_aluno(agent, draft):
    agent.docs = [CHUNK]
    agent.replies[Draft] = Draft(sufficient=True, answer=draft, used=[1])
    answer = graph.answer_question("pergunta")["answer"]
    assert answer.startswith("São correquisitos. Cursar junto.")


def test_calendario_indisponivel_avisa_sem_inventar_data(agent, monkeypatch):
    monkeypatch.setattr(graph, "calendario_atual", lambda: Resultado("indisponivel", mensagem="Confira o calendário oficial."))
    agent.replies[Classification] = Classification(intent="eureca", calendario=True)
    result = graph.answer_question("quando acaba o período?")
    assert result["answer"] == "Confira o calendário oficial."
    assert result["route"] == "eureca"
    assert agent.db.rows["escalations"] == []


def test_codigo_de_horario_e_lido_mesmo_fora_de_escopo_e_avisa_que_e_de_colegas(agent):
    agent.replies[Classification] = Classification(intent="fora_de_escopo")
    agent.replies[Draft] = Draft(sufficient=True, answer="Segunda e quarta, das 14h às 16h.", used=[1])
    result = graph.answer_question("o que significa 24T23?")
    assert result["route"] == "docs"
    assert graph.COMMUNITY_NOTICE in result["answer"]


def test_disciplina_nao_encontrada_segue_pela_busca_comum(agent, monkeypatch):
    monkeypatch.setattr(graph, "discipline_context", lambda names: [])
    agent.replies[Classification] = Classification(intent="faq_docs", categoria="x", disciplinas=["LLM"])
    agent.docs = [CHUNK]
    agent.replies[Draft] = Draft(sufficient=True, answer="A disciplina que trata desse assunto é Y.", used=[1])
    assert graph.answer_question("pré-requisito de llm?")["route"] == "docs"


@pytest.mark.parametrize("question", [
    "quais horarios dalton ta na coordenação?",
    "que horas encontro dalton?",
    "quando dalton atende?",
])
def test_horario_citando_pessoa_nao_vira_consulta_de_email(agent, question):
    agent.replies[Classification] = Classification(intent="contatos", categoria="contatos", contact_name="Dalton")
    agent.faq = [{**FAQ_HIT, "id": "horario", "answer": "Terças e quintas de manhã."}]
    agent.replies[FaqChoice] = FaqChoice(faq_id="horario")
    result = graph.answer_question(question)
    assert result["route"] == "faq"
    assert result["answer"].startswith("Terças e quintas")


def test_onde_encontrar_professor_usa_as_aulas_dele(agent, monkeypatch):
    aulas = {**CHUNK, "id": 7, "document_id": "eureca-horarios", "locator": "Docente Rohit Gheyi"}
    monkeypatch.setattr(graph, "professor_context", lambda names: [aulas] if names == ["rohit"] else [])
    agent.replies[Classification] = Classification(intent="faq_docs", categoria="x", professores=["rohit"])
    agent.replies[Draft] = Draft(sufficient=True, answer="Dá aula na quinta; a maioria das salas fica no bloco CN.", used=[1])
    answer = graph.answer_question("onde encontro rohit?")["answer"]
    assert "Horário do período 2026.2" in answer
    assert "Fonte: Horários do curso no Eureca, Docente Rohit Gheyi" in answer


def test_como_falar_com_pessoa_continua_em_contatos(agent):
    agent.replies[Classification] = Classification(intent="contatos", categoria="contatos", contact_name="Dalton")
    assert graph.answer_question("como eu consigo falar com dalton?")["route"] == "contatos"
    assert graph.answer_question("qual o email do dalton e onde encontro?")["route"] == "contatos"


def test_outros_documentos_nao_levam_o_aviso_do_ppc(agent):
    agent.docs = [CHUNK]
    agent.replies[Draft] = Draft(sufficient=True, answer="São 12 horas semanais.", used=[1])
    assert "SIGAA" not in graph.answer_question("quantas horas?")["answer"]


@pytest.mark.parametrize("draft", [
    Draft(sufficient=False, answer="texto", used=[1]),
    Draft(sufficient=True, answer="texto", used=[]),
    Draft(sufficient=True, answer="texto", used=[9]),
    Draft(sufficient=True, answer=" ", used=[1]),
])
def test_rascunho_sem_apoio_escala(agent, draft):
    agent.docs = [CHUNK]
    agent.replies[Draft] = draft
    result = graph.answer_question("pergunta")
    assert result["route"] == "escalated"
    assert result["answer"] == graph.ESCALATED_ANSWER


@pytest.mark.parametrize("verification", [
    Verification(supported=False, answers_question=True),
    Verification(supported=True, answers_question=False),
])
def test_verificacao_reprovada_escala(agent, verification):
    agent.docs = [CHUNK]
    agent.replies[Draft] = Draft(sufficient=True, answer="Inventado.", used=[1])
    agent.replies[Verification] = verification
    result = graph.answer_question("pergunta")
    assert result["route"] == "escalated"
    assert "Inventado" not in result["answer"]


def test_sem_resultados_cria_escalonamento_ligado_a_conversa(agent):
    result = graph.answer_question("o RU abre no sábado?")
    assert result["route"] == "escalated"
    assert agent.db.rows["escalations"][0]["conversation_id"] == result["conversation_id"]
    assert result["escalation_id"] == agent.db.rows["escalations"][0]["id"]


def test_fora_de_escopo_nao_busca_nem_escala(agent):
    agent.replies[Classification] = Classification(intent="fora_de_escopo", categoria="outros")
    result = graph.answer_question("qual a capital da França?")
    assert result["route"] == "out_of_scope"
    assert agent.db.rows["escalations"] == []


def test_contato_encontrado(agent):
    agent.replies[Classification] = Classification(intent="contatos", categoria="contatos", contact_name="Dalton")
    result = graph.answer_question("qual o email do Dalton?")
    assert result["route"] == "contatos"
    assert search_contacts("dalton")[0]["email_principal"] in result["answer"]


def test_contato_inexistente_escala(agent):
    agent.replies[Classification] = Classification(intent="contatos", categoria="contatos", contact_name="Fulano")
    assert graph.answer_question("email do Fulano?")["route"] == "escalated"


def test_busca_de_contatos_ignora_acento_e_titulo():
    assert search_contacts("prof. claudio campelo")[0]["email_alternativo"] == search_contacts("claudio campelo")[0]["email_alternativo"] != ""
    assert search_contacts("klebia")[0]["tipo"] == "secretaria"
    assert search_contacts("") == []
    assert search_contacts("prof") == []


def test_cli_lembra_o_curriculo_citado(monkeypatch):
    calls = []

    def fake(question, curriculum=None, channel="cli"):
        calls.append(curriculum)
        return {"answer": "resposta"}

    monkeypatch.setattr(cli, "answer_question", fake)
    session = cli.Session()
    cli.handle_message(session, "quantos créditos preciso?")
    cli.handle_message(session, "sou do currículo 2023")
    cli.handle_message(session, "e de extensão?")
    assert calls == [None, "2023", "2023"]
