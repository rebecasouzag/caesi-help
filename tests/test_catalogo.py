import pytest

from app.base.config import DATA_DIR
from app.conhecimento import catalogo, entidades, manifest, requisitos
from app.conhecimento.catalogo import get_catalog, resolve_discipline, resolve_person
from app.ferramentas import academico
from app.ferramentas.contatos import search_contacts
from app.ingestao import validar_dados

REQUIREMENTS = {
    "A": {"pre": [], "co": []},
    "B": {"pre": ["A"], "co": []},
    "C": {"pre": ["B"], "co": []},
    "D": {"pre": [], "co": ["E"]},
    "E": {"pre": [], "co": ["D"]},
    "F": {"pre": [], "co": []},
}


@pytest.fixture(scope="module")
def real():
    return get_catalog().requirements


def discipline_name(mention):
    catalog = get_catalog()
    result = resolve_discipline(mention, catalog)
    return catalog.disciplines[int(result.keys[0])].name if result.status == "resolvido" else result.status


def person_name(mention):
    catalog = get_catalog()
    result = resolve_person(mention, catalog)
    return catalog.people_names[result.keys[0]] if result.status == "resolvido" else result.status


def test_dados_reais_se_ligam_sem_erros():
    assert validar_dados.validate() == []


def test_cadeia_direta_e_indireta():
    assert requisitos.prerequisite_chain("A", "B", REQUIREMENTS) == ["A", "B"]
    assert requisitos.prerequisite_chain("A", "C", REQUIREMENTS) == ["A", "B", "C"]
    assert requisitos.prerequisite_chain("C", "A", REQUIREMENTS) is None


@pytest.mark.parametrize("first, second, expected", [
    ("A", "B", "pré-requisito direto"),
    ("B", "A", "pré-requisito direto"),
    ("A", "C", "pré-requisito indireto"),
    ("C", "A", "pré-requisito indireto"),
    ("D", "E", "correquisitos"),
    ("A", "F", "Podem ser cursadas"),
])
def test_relacao_entre_disciplinas(first, second, expected):
    assert expected in requisitos.describe_relation(first, second, REQUIREMENTS)


def test_correcao_do_ca_acrescenta_es_e_mantem_psoft_em_analise(real):
    assert set(real["Análise de Sistemas"]["pre"]) == {"Projeto de Software", "Engenharia de Software"}
    text = requisitos.describe_relation("Engenharia de Software", "Análise de Sistemas", real)
    assert "Engenharia de Software é pré-requisito direto de Análise de Sistemas" in text


def test_requisitos_reais_foram_reconstruidos_do_ppc(real):
    assert real["Programação II"]["pre"] == ["Programação I", "Laboratório de Programação I"]
    assert real["Programação II"]["co"] == ["Laboratório de Programação II"]
    assert real["Programação I"]["pre"] == []
    assert real["Projeto de Software"]["pre"] == ["Estruturas de Dados e Algoritmos", "Laboratório de Estruturas de Dados e Algoritmos"]
    assert real["Programação para a Web II"]["pre"] == ["Programação para a Web I"]


def test_relacao_real_indireta(real):
    text = requisitos.describe_relation("Programação I", "Análise de Sistemas", real)
    assert "pré-requisito indireto" in text
    assert text.index("Programação I") < text.index("Análise de Sistemas")


def test_relacao_real_correquisito(real):
    assert "correquisitos" in requisitos.describe_relation("Programação I", "Laboratório de Programação I", real)


@pytest.mark.parametrize("mention, expected", [
    ("analise de sistemas", "Análise de Sistemas"),
    ("Engenharia de Software", "Engenharia de Software"),
    ("eda", "Estruturas de Dados e Algoritmos"),
    ("P2", "Programação II"),
    ("psoft", "Projeto de Software"),
    ("leda", "Laboratório de Estruturas de Dados e Algoritmos"),
    ("lab de eda", "Laboratório de Estruturas de Dados e Algoritmos"),
    ("plp", "Paradigmas de Linguagens de Programação"),
    ("atal", "Análise e Técnicas de Algoritmos"),
    ("programação 2", "Programação II"),
    ("banco de dados 1", "Banco de Dados I"),
    ("bd", "Banco de Dados I"),
    ("calc 1", "Cálculo Diferencial e Integral I"),
    ("llm", "TECC(Modelos de Linguagem de Grande Escala)"),
    ("redes", "Redes de Computadores"),
    ("programação", "ambiguo"),
    ("banco", "ambiguo"),
    ("disciplina inexistente xyz", "nao_encontrado"),
    ("", "nao_encontrado"),
])
def test_resolve_disciplina(mention, expected):
    assert discipline_name(mention) == expected


@pytest.mark.parametrize("mention, expected", [
    ("rohit", "Rohit Gheyi"),
    ("professor rohit", "Rohit Gheyi"),
    ("Thiago Nóbrega", "Thiago Pereira da Nóbrega"),
    ("massoni", "Tiago Lima Massoni"),
    ("thiago emanuel", "Thiago Emmanuel Pereira da Cunha Silva"),
    ("thiago emannuel", "Thiago Emmanuel Pereira da Cunha Silva"),
    ("melina", "Melina Mongiovi Brito Lira"),
    ("thiago", "ambiguo"),
    ("carlos", "ambiguo"),
    ("Fulano", "nao_encontrado"),
    ("graicy", "nao_encontrado"),
])
def test_resolve_pessoa(mention, expected):
    assert person_name(mention) == expected


@pytest.mark.parametrize("text, expected", [
    ("quem da aula de psoft?", ["psoft"]),
    ("posso pagar eda e leda?", ["eda", "leda"]),
    ("qual o horario de lab de p1?", ["labdep1"]),
    ("qual a ementa de Teoria dos Grafos", ["grafos", "Teoria dos Grafos"]),
    ("as disciplinas so abrem em es", []),
    ("como faço matricula?", []),
])
def test_reconhece_disciplina_no_texto_sem_llm(text, expected):
    assert catalogo.find_discipline_mentions(text) == expected


def test_nome_exato_vence_a_semelhanca():
    assert entidades.resolve("cálculo i", {"1": "Cálculo I", "2": "Cálculo II"}, {}).keys == ["1"]


def test_mesmo_nome_com_dois_codigos_vira_uma_disciplina():
    catalog = get_catalog()
    versions = [d for d in catalog.disciplines.values() if d.name == "Metodologia Científica"]
    assert len(versions) == 1
    assert len(versions[0].codes) == 2


def test_nome_do_eureca_em_maiusculas_fica_legivel():
    assert catalogo.title_case("TECC(MODELOS DE LINGUAGEM DE GRANDE ESCALA)") == "TECC(Modelos de Linguagem de Grande Escala)"
    assert catalogo.title_case("ÁLGEBRA LINEAR I") == "Álgebra Linear I"


def test_laboratorio_sem_horario_no_eureca_usa_o_horario_transcrito():
    catalog = get_catalog()
    code = int(resolve_discipline("lp1", catalog).keys[0])
    days = {m.day for m in catalog.meetings if m.code == code and m.turma == "01"}
    assert days == {"segunda", "quarta"}


def test_disciplina_sem_horario_em_lugar_nenhum_nao_inventa():
    meeting = catalogo.Meeting(1, "01", None, None, None, "sala")
    assert academico.format_meeting(meeting) == "dia e hora não informados"


def test_contexto_de_disciplina_traz_docentes_com_nome_completo():
    chunks = academico.discipline_context(["psoft"])
    schedule = next(c for c in chunks if c["document_id"] == academico.SCHEDULE_DOC)
    assert "Fábio Jorge Almeida Morais" in schedule["content"]
    assert "Giovanni Farias da Silva" in schedule["content"]


def test_contexto_de_disciplina_fora_do_ppc_avisa_que_nao_tem_requisitos():
    facts = academico.discipline_context(["llm"])[0]
    assert "não constam no PPC" in facts["content"]


def test_contexto_de_professor_traz_funcao_email_e_bloco_cn():
    facts, schedule = academico.professor_context(["thiago emanuel"])
    assert "Coordenador Administrativo" in facts["content"] and "professor" in facts["content"]
    assert "@" in facts["content"]
    assert "Aulas em" in schedule["content"]
    assert "bloco CN" in academico.professor_context(["rohit"])[0]["content"]


def test_sala_relatada_por_colegas_vem_identificada():
    facts = academico.professor_context(["massoni"])[0]["content"]
    assert "segundo colegas (não oficial): SPLab" in facts
    assert "bloco CN" not in facts


def test_nome_ambiguo_gera_pergunta():
    question = academico.clarification([], ["thiago"])
    assert "Thiago Emmanuel" in question and "Thiago Pereira da Nóbrega" in question
    assert academico.clarification(["eda"], ["rohit"]) is None


def test_muitas_opcoes_pede_nome_mais_completo():
    assert "nome mais completo" in academico.clarification(["programação"], [])


def test_contato_de_pessoa_sem_email_nao_e_devolvido():
    assert search_contacts("Amauri Fragoso") == []


def test_busca_de_contatos_traz_os_dois_quando_ambiguo():
    assert {p["nome"] for p in search_contacts("thiago")} == {"Thiago Emmanuel Pereira da Cunha Silva", "Thiago Pereira da Nóbrega"}


def test_faq_de_horario_traz_terca_e_quinta():
    faq = {e.id: e for e in manifest.load_faq(DATA_DIR / "faq.yaml")}
    entry = faq["horario-atendimento-coordenacao"]
    assert "terças ou quintas de manhã" in " ".join(entry.resposta.split())
    assert entry.fontes[0].doc == "site-inicial.md"


def test_faq_comunidade_nunca_esta_como_revisado():
    entries = manifest.load_faq(DATA_DIR / "faq-comunidade.yaml")
    assert entries and all(e.status != "revisado" for e in entries)
