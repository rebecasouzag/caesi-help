"""Ferramentas sobre disciplinas e pessoas. Os dados vêm do catálogo; a regra de pré-requisito é calculada em código."""
from itertools import combinations

from app.conhecimento.catalogo import WEEKDAY_NUMBERS, Catalog, Discipline, Meeting, get_catalog, resolve_discipline, resolve_person
from app.conhecimento.requisitos import describe_relation
from app.ferramentas.evidencia import Evidencia, Resultado
from app.ferramentas.normas import ler_artigo

DISCIPLINES_DOC = "disciplinas.json"
DISCIPLINES_TITLE = "Disciplinas do curso (PPC 2023)"
SCHEDULE_DOC = "eureca-horarios"
PEOPLE_DOC = "catalogo/pessoas.csv"
# Regra da flexibilização de pré-requisito no Regulamento de Graduação.
PREREQUISITE_RULE = ("res_11_2024-cse.pdf", "Art. 147")
PROFESSOR_ROOMS = "bloco CN"  # informado pelo CA
SOURCE_TITLES = {
    SCHEDULE_DOC: "Horários do curso no Eureca",
    PEOPLE_DOC: "Tabela de contatos do curso",
}
MAX_OPTIONS = 5


def source_notices(period: str | None = None) -> dict[str, str]:
    period = period or get_catalog().period
    return {SCHEDULE_DOC: f"Horário do período {period}; confira mudanças em cgcc.ufcg.edu.br/horarios."}


def evidence(fonte: str, titulo: str, trecho: str, texto: str, procedencia: str = "oficial") -> Evidencia:
    return Evidencia(f"{fonte}:{trecho}", texto, fonte, titulo, trecho, procedencia=procedencia)


def format_meeting(meeting: Meeting) -> str:
    if not meeting.day:
        return "dia e hora não informados"
    return f"{meeting.day}, {meeting.start:02d}-{meeting.end:02d}h"


def meeting_order(meeting: Meeting) -> tuple[int, int]:
    return WEEKDAY_NUMBERS.get(meeting.day, 99), meeting.start or 0


def discipline_facts(discipline: Discipline, catalog: Catalog) -> Evidencia:
    curricula = " e ".join(discipline.curricula)
    kind = {"OBRIGATORIO": "obrigatória", "OPTATIVO": "optativa", "COMPLEMENTAR": "complementar"}.get(discipline.kind, discipline.kind.lower())
    lines = [f"Disciplina: {discipline.name}", f"Currículos: {curricula}. Tipo: {kind}."]
    if discipline.semester:
        lines.append(f"Semestre ideal: {discipline.semester}º.")
    lines.append(f"Carga horária: {discipline.hours} horas. Créditos: {discipline.credits}.")
    requirements = catalog.requirements.get(discipline.name)
    if requirements:
        lines.append(f"Pré-requisitos: {'; '.join(requirements['pre']) or 'nenhum'}.")
        lines.append(f"Correquisitos: {'; '.join(requirements['co']) or 'nenhum'}.")
    else:
        lines.append("Pré-requisitos: não constam no PPC 2023 da base.")
    if discipline.summary:
        lines.append(f"Ementa: {discipline.summary}")
    return evidence(DISCIPLINES_DOC, DISCIPLINES_TITLE, discipline.name, "\n".join(lines))


def discipline_schedule(discipline: Discipline, catalog: Catalog) -> Evidencia | None:
    by_turma = {}
    for meeting in catalog.meetings:
        if meeting.code == discipline.code:
            by_turma.setdefault(meeting.turma, []).append(meeting)
    if not by_turma:
        return None
    lines = [f"Disciplina: {discipline.name}. Horário {catalog.period}:"]
    for turma, meetings in sorted(by_turma.items()):
        teacher_ids = catalog.teachers.get((discipline.code, turma), [])
        teachers = ", ".join(catalog.people_names[pid] for pid in teacher_ids) or "não informado"
        rooms = ", ".join(sorted({m.room for m in meetings if m.room})) or "não informada"
        times = "; ".join(format_meeting(m) for m in sorted(meetings, key=meeting_order))
        lines.append(f"- Turma {turma}, docente: {teachers}, sala {rooms}: {times}")
    return evidence(SCHEDULE_DOC, SOURCE_TITLES[SCHEDULE_DOC], f"Horário de {discipline.name}", "\n".join(lines))


def person_facts(pid: str, catalog: Catalog) -> Evidencia:
    person = catalog.people[pid]
    lines = [f"Pessoa: {person['nome']}."]
    if person["cargos"]:
        lines.append(f"Função: {person['cargos']}.")
    if person["email_principal"]:
        emails = person["email_principal"] + (f" ou {person['email_alternativo']}" if person["email_alternativo"] else "")
        lines.append(f"E-mail: {emails}.")
    if person["funcao_relatada"]:
        lines.append(f"Segundo colegas (não oficial): {person['funcao_relatada']}.")
    if person["sala_relatada"]:
        lines.append(f"Sala, segundo colegas (não oficial): {person['sala_relatada']}.")
    elif person["departamento"] == "computacao":
        lines.append(f"A sala individual não está na base; a maioria das salas dos professores de computação fica no {PROFESSOR_ROOMS}.")
    elif person["tipo"] == "professor":
        lines.append("Docente de outro departamento; a sala individual não está na base.")
    return evidence(PEOPLE_DOC, SOURCE_TITLES[PEOPLE_DOC], person["nome"], "\n".join(lines))


def person_schedule(pid: str, catalog: Catalog) -> Evidencia | None:
    lines = []
    for discipline_code, turma in sorted(key for key, teachers in catalog.teachers.items() if pid in teachers):
        meetings = [m for m in catalog.meetings if m.code == discipline_code and m.turma == turma]
        rooms = ", ".join(sorted({m.room for m in meetings if m.room})) or "não informada"
        times = "; ".join(format_meeting(m) for m in sorted(meetings, key=meeting_order)) or "dia e hora não informados"
        lines.append(f"- {catalog.disciplines[discipline_code].name} (turma {turma}), sala {rooms}: {times}")
    if not lines:
        return None
    name = catalog.people_names[pid]
    content = f"Docente: {name}.\nAulas em {catalog.period}:\n" + "\n".join(lines)
    return evidence(SCHEDULE_DOC, SOURCE_TITLES[SCHEDULE_DOC], f"Docente {name}", content)


def buscar_disciplina(nome: str) -> Resultado:
    """Dados e horário de uma disciplina: currículos, tipo, carga horária, requisitos, ementa, turmas e docentes."""
    catalog = get_catalog()
    resolution = resolve_discipline(nome, catalog)
    if resolution.status == "ambiguo":
        return Resultado("ambiguo", opcoes=sorted(catalog.discipline_names[key] for key in resolution.keys))
    if resolution.status == "nao_encontrado":
        return Resultado("nao_encontrado")
    discipline = catalog.disciplines[int(resolution.keys[0])]
    schedule = discipline_schedule(discipline, catalog)
    return Resultado("encontrado", [discipline_facts(discipline, catalog), *([schedule] if schedule else [])])


def buscar_pessoa(nome: str) -> Resultado:
    """Função, contato e aulas de uma pessoa do curso."""
    catalog = get_catalog()
    resolution = resolve_person(nome, catalog)
    if resolution.status == "ambiguo":
        return Resultado("ambiguo", opcoes=sorted(catalog.people_names[key] for key in resolution.keys))
    if resolution.status == "nao_encontrado":
        return Resultado("nao_encontrado")
    pid = resolution.keys[0]
    schedule = person_schedule(pid, catalog)
    return Resultado("encontrado", [person_facts(pid, catalog), *([schedule] if schedule else [])])


def horario_de(nome: str) -> Resultado:
    """Horário de uma disciplina ou, se o nome não for de disciplina, as aulas de uma pessoa."""
    catalog = get_catalog()
    resolution = resolve_discipline(nome, catalog)
    if resolution.status == "resolvido":
        schedule = discipline_schedule(catalog.disciplines[int(resolution.keys[0])], catalog)
        return Resultado("encontrado", [schedule]) if schedule else Resultado("nao_encontrado")
    person = buscar_pessoa(nome)
    if person.status == "encontrado":
        schedules = [e for e in person.evidencias if e.fonte == SCHEDULE_DOC]
        return Resultado("encontrado", schedules) if schedules else Resultado("nao_encontrado")
    if resolution.status == "ambiguo":
        return Resultado("ambiguo", opcoes=sorted(catalog.discipline_names[key] for key in resolution.keys))
    return person


def relacao_entre_disciplinas(nomes: list[str]) -> Resultado:
    """Se as disciplinas podem ser cursadas juntas, calculado pelos requisitos, com a regra de flexibilização."""
    catalog = get_catalog()
    resolved = {}
    for nome in nomes:
        resolution = resolve_discipline(nome, catalog)
        if resolution.status == "ambiguo":
            return Resultado("ambiguo", opcoes=sorted(catalog.discipline_names[key] for key in resolution.keys))
        if resolution.status == "resolvido":
            name = catalog.disciplines[int(resolution.keys[0])].name
            resolved[name] = name
    if len(resolved) < 2:
        return Resultado("nao_encontrado")
    ordered = sorted(resolved)
    text = "\n".join(describe_relation(a, b, catalog.requirements) for a, b in combinations(ordered, 2))
    relation = evidence(DISCIPLINES_DOC, DISCIPLINES_TITLE, "Relação entre " + ", ".join(ordered), text)
    return Resultado("encontrado", [relation, *ler_artigo(*PREREQUISITE_RULE).evidencias])


def discipline_context(mentions: list[str]) -> list[dict]:
    """Trechos para o grafo atual: dados das disciplinas citadas, horário e a relação entre elas."""
    facts, schedules, names = [], [], []
    for mention in mentions:
        result = buscar_disciplina(mention)
        if result.status == "encontrado" and result.evidencias[0].trecho not in names:
            names.append(result.evidencias[0].trecho)
            facts.append(result.evidencias[0])
            schedules += result.evidencias[1:]
    evidences = facts + schedules
    if len(names) > 1:
        evidences += relacao_entre_disciplinas(names).evidencias
    return [e.as_chunk() for e in evidences]


def professor_context(mentions: list[str]) -> list[dict]:
    """Trechos para o grafo atual: função, contato e aulas das pessoas citadas."""
    facts, schedules, seen = [], [], set()
    for mention in mentions:
        result = buscar_pessoa(mention)
        if result.status == "encontrado" and result.evidencias[0].id not in seen:
            seen.add(result.evidencias[0].id)
            facts.append(result.evidencias[0])
            schedules += result.evidencias[1:]
    return [e.as_chunk() for e in facts + schedules]


def clarification(disciplines: list[str], professors: list[str]) -> str | None:
    """Pergunta a fazer ao aluno se algum nome citado aponta para mais de uma disciplina ou pessoa."""
    for mention, tool in [*((m, buscar_disciplina) for m in disciplines), *((m, buscar_pessoa) for m in professors)]:
        result = tool(mention)
        if result.status == "ambiguo":
            return ask_which(mention, result.opcoes)
    return None


def ask_which(mention: str, options: list[str]) -> str:
    if len(options) > MAX_OPTIONS:
        return f'"{mention}" pode ser várias opções. Pode dar o nome mais completo?'
    return f"Você quis dizer {' ou '.join(options)}?"
