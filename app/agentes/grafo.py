"""Grafo do agente. Uso: answer_question("posso estagiar no 3º período?")."""
import os
import re
from functools import lru_cache
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.base.config import DATA_DIR
from app.base.db import get_client
from app.base.llm import ask
from app.conhecimento.busca import answerable, search_docs, search_faq
from app.conhecimento.catalogo import find_discipline_mentions
from app.conhecimento.manifest import load_manifest
from app.ferramentas.academico import PEOPLE_DOC, SOURCE_TITLES, clarification, discipline_context, professor_context, source_notices
from app.ferramentas.calendario import CALENDAR_DOC, CALENDAR_TITLE, SIGAA_DOC, calendario_atual, decodificar_horario_sigaa
from app.ferramentas.contatos import search_contacts
from app.prompts import load_prompt

load_dotenv()

# As notas deste modelo são altas mesmo sem relação entre os textos: os limiares só selecionam candidatos.
# Quem decide se o conteúdo responde à pergunta é validar_faq, redigir_com_fontes e verificar.
LIMIAR_FAQ = float(os.getenv("LIMIAR_FAQ", "0.85"))
LIMIAR_DOCS = float(os.getenv("LIMIAR_DOCS", "0.80"))
MAX_EXTRA_CHUNKS = 3
SCHEDULE_RE = re.compile(r"hor[aá]rio|\bhoras?\b|atende|atendimento|quando|que dia|\bsala\b|\bonde\b", re.IGNORECASE)
CONTACT_RE = re.compile(r"e-?mail|contato", re.IGNORECASE)
TRECHO_REF_RE = re.compile(r"\s*(?:\[\d+\]|【\d+】)")
CURRICULUM_RE = re.compile(r"(?:curr[ií]culo|grade)\D{0,12}\b(2017|2023)\b|\b(2017|2023)\b\D{0,12}(?:curr[ií]culo|grade)", re.IGNORECASE)

ESCALATED_ANSWER = "Não tenho certeza, encaminhei sua pergunta para o CAESI."
OUT_OF_SCOPE_ANSWER = (
    "Só consigo ajudar com dúvidas sobre o curso de Ciência da Computação da UFCG, "
    "como matrícula, trancamento, estágio, atividades complementares, TCC e monitoria."
)
CONFIRM_NOTICE = "Atenção: confirme com a coordenação antes de seguir esta orientação."
COMMUNITY_PREFIX = "Segundo colegas (informação não oficial):"
COMMUNITY_NOTICE = "Informação de colegas, não oficial: confirme com a coordenação."

CLASSIFY_PROMPT = load_prompt("classificar")

SELECT_FAQ_PROMPT = load_prompt("selecionar_faq")

DRAFT_PROMPT = load_prompt("redigir")

VERIFY_PROMPT = load_prompt("verificar")


class Classification(BaseModel):
    intent: Literal["faq_docs", "contatos", "eureca", "fora_de_escopo"]
    categoria: str | None = None
    contact_name: str | None = None
    disciplinas: list[str] = Field(default_factory=list)
    professores: list[str] = Field(default_factory=list)
    calendario: bool = False


class FaqChoice(BaseModel):
    faq_id: str | None = None


class Draft(BaseModel):
    sufficient: bool
    answer: str = ""
    used: list[int] = Field(default_factory=list)


class Verification(BaseModel):
    supported: bool
    answers_question: bool


class AgentState(TypedDict, total=False):
    question: str
    channel: str
    curriculum: str | None
    intent: str
    contact_name: str | None
    disciplines: list[str]
    professors: list[str]
    calendar: bool
    candidates: list[dict]
    selected: dict | None
    chunks: list[dict]
    answer: str
    route: str
    confidence: float | None
    sources: list[dict]
    conversation_id: str | None
    escalation_id: str | None


@lru_cache
def document_titles() -> dict[str, str]:
    titles = {
        d.arquivo: f"{d.titulo} ({d.url.removeprefix('https://').rstrip('/')})" if d.url else d.titulo
        for d in load_manifest(DATA_DIR / "manifest.yaml")
    }
    return {**titles, **SOURCE_TITLES, CALENDAR_DOC: CALENDAR_TITLE}


@lru_cache
def document_notices() -> dict[str, str]:
    manifest_notices = {d.arquivo: d.aviso for d in load_manifest(DATA_DIR / "manifest.yaml") if d.aviso}
    return {**manifest_notices, **source_notices(), SIGAA_DOC: COMMUNITY_NOTICE}


def format_notices(sources: list[dict]) -> list[str]:
    notices = document_notices()
    return list(dict.fromkeys(notices[s["doc"]] for s in sources if s["doc"] in notices))


def format_sources(sources: list[dict]) -> str:
    titles = document_titles()
    parts = [f"{titles.get(s['doc'], s['doc'])}, {s['trecho']}" for s in sources]
    return "Fonte: " + "; ".join(parts)


def detect_curriculum(text: str) -> str | None:
    """Só aceita o ano citado junto de currículo ou grade: "Res. 01/2023" não é um currículo."""
    match = CURRICULUM_RE.search(text)
    return (match.group(1) or match.group(2)) if match else None


def format_chunks(chunks: list[dict]) -> str:
    return "\n\n".join(
        f"[{number}] {c['title']} (status: {c['status']}), {c['locator']}\n{c['content']}"
        for number, c in enumerate(chunks, start=1)
    )


def classificar(state: AgentState) -> AgentState:
    result = ask(Classification, CLASSIFY_PROMPT, state["question"])
    return {
        "intent": result.intent,
        "contact_name": result.contact_name,
        "disciplines": result.disciplinas,
        "professors": result.professores,
        "calendar": result.calendario,
        "curriculum": state.get("curriculum") or detect_curriculum(state["question"]),
    }


def buscar_faq(state: AgentState) -> AgentState:
    curriculum = state.get("curriculum")
    hits = [h for h in search_faq(state["question"]) if h["similarity"] >= LIMIAR_FAQ and answerable(h)]
    # Uma entrada exclusiva de um currículo não serve para quem declarou o outro.
    candidates = [h for h in hits if not (curriculum and h["curriculum"] in ("2017", "2023") and h["curriculum"] != curriculum)]
    # Em datas, o Eureca vale mais do que relato de colegas.
    if state.get("calendar"):
        candidates = [h for h in candidates if h.get("provenance") != "comunidade"]
    return {"candidates": candidates}


def validar_faq(state: AgentState) -> AgentState:
    listing = "\n\n".join(
        f"id: {c['id']}\nOrigem: {c.get('provenance', 'oficial')}\nPergunta: {c['question']}\nResposta: {c['answer']}"
        for c in state["candidates"]
    )
    choice = ask(FaqChoice, SELECT_FAQ_PROMPT, f"Pergunta do aluno: {state['question']}\n\nEntradas:\n{listing}")
    return {"selected": next((c for c in state["candidates"] if c["id"] == choice.faq_id), None)}


def responder_faq(state: AgentState) -> AgentState:
    entry = state["selected"]
    # Sem currículo informado, entry["answer"] traz as duas opções.
    by_curriculum = entry.get("answer_by_curriculum") or {}
    parts = [by_curriculum.get(state.get("curriculum")) or entry["answer"]]
    if entry["curriculum"] in ("2017", "2023"):
        parts.insert(0, f"Vale para o currículo {entry['curriculum']}.")
    sources = list(entry["sources"])
    if state.get("calendar"):
        calendar = calendario_atual()
        parts += [e.texto for e in calendar.evidencias] or [calendar.mensagem]
        sources += [{"doc": e.fonte, "trecho": e.trecho} for e in calendar.evidencias]
    community = entry.get("provenance") == "comunidade"
    if community:
        parts.insert(0, COMMUNITY_PREFIX)
    if community or entry["status"] == "confirmar":
        parts.append(CONFIRM_NOTICE)
    if sources:
        parts.append(format_sources(sources))
    return {
        "answer": "\n\n".join(parts),
        "route": "faq",
        "confidence": entry["similarity"],
        "sources": sources,
    }


def buscar_docs(state: AgentState) -> AgentState:
    mentioned = list(dict.fromkeys([*(state.get("disciplines") or []), *find_discipline_mentions(state["question"])]))
    question = clarification(mentioned, state.get("professors") or [])
    if question:
        return {"answer": question, "route": "ambiguo", "chunks": [], "sources": []}
    tool_chunks = []
    if state.get("calendar"):
        calendar = calendario_atual()
        if calendar.status == "indisponivel":
            return {"answer": calendar.mensagem, "route": "eureca", "chunks": [], "sources": []}
        tool_chunks += [e.as_chunk() for e in calendar.evidencias]
    tool_chunks += [e.as_chunk() for e in decodificar_horario_sigaa(state["question"]).evidencias]
    context = tool_chunks + (discipline_context(mentioned) if mentioned else [])
    if state.get("professors"):
        context += professor_context(state["professors"])
    known = {c["id"] for c in context}
    hits = [c for c in search_docs(state["question"]) if c["similarity"] >= LIMIAR_DOCS and c["id"] not in known]
    # Com as disciplinas já identificadas, a busca semântica só complementa.
    return {"chunks": context + (hits[:MAX_EXTRA_CHUNKS] if context else hits)}


def redigir_com_fontes(state: AgentState) -> AgentState:
    chunks = state["chunks"]
    user = f"Pergunta: {state['question']}\nCurrículo do aluno: {state.get('curriculum') or 'não informado'}\n\nTrechos:\n{format_chunks(chunks)}"
    draft = ask(Draft, DRAFT_PROMPT, user)
    used = [chunks[n - 1] for n in dict.fromkeys(draft.used) if 1 <= n <= len(chunks)]
    if not draft.sufficient or not used or not draft.answer.strip():
        return {"answer": "", "chunks": []}
    sources = [{"doc": c["document_id"], "trecho": c["locator"]} for c in used]
    return {
        "answer": draft.answer.strip(),
        "chunks": used,
        "sources": sources,
        "route": "docs",
        "confidence": max(c["similarity"] for c in used),
    }


def verificar(state: AgentState) -> AgentState:
    user = f"Pergunta: {state['question']}\n\nResposta: {state['answer']}\n\nTrechos:\n{format_chunks(state['chunks'])}"
    result = ask(Verification, VERIFY_PROMPT, user)
    if not (result.supported and result.answers_question):
        return {"answer": "", "chunks": []}
    # Os números dos trechos são internos: o aluno vê a fonte no final.
    answer = TRECHO_REF_RE.sub("", state["answer"]).strip()
    parts = [answer, *format_notices(state["sources"]), format_sources(state["sources"])]
    return {"answer": "\n\n".join(parts)}


def contatos(state: AgentState) -> AgentState:
    rows = search_contacts(state.get("contact_name") or "")
    if not rows:
        return {"answer": ""}
    lines = [
        f"{r['nome']}: {r['email_principal']}" + (f" ou {r['email_alternativo']}" if r["email_alternativo"] else "")
        for r in rows
    ]
    sources = [{"doc": PEOPLE_DOC, "trecho": r["nome"]} for r in rows]
    return {
        "answer": "\n".join(lines) + "\n\nFonte: Tabela de contatos do curso",
        "route": "contatos",
        "sources": sources,
    }


def fora_de_escopo(state: AgentState) -> AgentState:
    return {"answer": OUT_OF_SCOPE_ANSWER, "route": "out_of_scope", "sources": []}


def escalar(state: AgentState) -> AgentState:
    return {"answer": ESCALATED_ANSWER, "route": "escalated", "confidence": None, "sources": []}


def registrar(state: AgentState) -> AgentState:
    client = get_client()
    conversation = client.table("conversations").insert({
        "channel": state.get("channel", "cli"),
        "question": state["question"],
        "answer": state["answer"],
        "route": state["route"],
        "confidence": state.get("confidence"),
        "sources": state.get("sources", []),
    }).execute().data[0]
    escalation_id = None
    if state["route"] == "escalated":
        escalation = client.table("escalations").insert({"conversation_id": conversation["id"]}).execute().data[0]
        escalation_id = escalation["id"]
    return {"conversation_id": conversation["id"], "escalation_id": escalation_id}


def route_intent(state: AgentState) -> str:
    question = state["question"]
    # O código de horário tem regra fixa: não depende do FAQ nem do classificador.
    if decodificar_horario_sigaa(question).status == "encontrado":
        return "buscar_docs"
    # Horário e local não estão na tabela de contatos, mesmo que a pergunta cite uma pessoa.
    if state["intent"] == "contatos" and SCHEDULE_RE.search(question) and not CONTACT_RE.search(question):
        return "buscar_faq"
    # Perguntas sobre disciplinas específicas vão aos dados delas, não às regras gerais do FAQ.
    if state["intent"] in ("faq_docs", "eureca") and state.get("disciplines"):
        return "buscar_docs"
    # O Eureca entra na etapa 5; até lá, essas perguntas seguem pela base local.
    return {"contatos": "contatos", "fora_de_escopo": "fora_de_escopo"}.get(state["intent"], "buscar_faq")


def route_candidates(state: AgentState) -> str:
    return "validar_faq" if state["candidates"] else "buscar_docs"


def route_selected(state: AgentState) -> str:
    return "responder_faq" if state.get("selected") else "buscar_docs"


def route_chunks(state: AgentState) -> str:
    if state.get("answer"):  # já respondido: pergunta de volta ou fonte indisponível
        return "registrar"
    return "redigir_com_fontes" if state["chunks"] else "escalar"


def route_answer(next_node: str):
    def route(state: AgentState) -> str:
        return next_node if state["answer"] else "escalar"
    return route


def build_graph():
    graph = StateGraph(AgentState)
    for node in (classificar, buscar_faq, validar_faq, responder_faq, buscar_docs,
                 redigir_com_fontes, verificar, contatos, fora_de_escopo, escalar, registrar):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "classificar")
    graph.add_conditional_edges("classificar", route_intent, ["buscar_faq", "buscar_docs", "contatos", "fora_de_escopo"])
    graph.add_conditional_edges("buscar_faq", route_candidates, ["validar_faq", "buscar_docs"])
    graph.add_conditional_edges("validar_faq", route_selected, ["responder_faq", "buscar_docs"])
    graph.add_conditional_edges("buscar_docs", route_chunks, ["redigir_com_fontes", "escalar", "registrar"])
    graph.add_conditional_edges("redigir_com_fontes", route_answer("verificar"), ["verificar", "escalar"])
    graph.add_conditional_edges("verificar", route_answer("registrar"), ["registrar", "escalar"])
    graph.add_conditional_edges("contatos", route_answer("registrar"), ["registrar", "escalar"])
    for node in ("responder_faq", "fora_de_escopo", "escalar"):
        graph.add_edge(node, "registrar")
    graph.add_edge("registrar", END)
    return graph.compile()


@lru_cache
def get_graph():
    return build_graph()


def answer_question(question: str, curriculum: str | None = None, channel: str = "cli") -> AgentState:
    return get_graph().invoke({"question": question, "curriculum": curriculum, "channel": channel})
