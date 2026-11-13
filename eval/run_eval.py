"""Avaliação do bot com perguntas e gabaritos.

Uso:
  python -m eval.run_eval --recuperacao   só a busca: sem LLM e sem custo
  python -m eval.run_eval --rapido        fluxo completo nas perguntas marcadas com rapido
  python -m eval.run_eval                 fluxo completo em todo o conjunto de desenvolvimento
  python -m eval.run_eval --final         fluxo completo nas perguntas reservadas

As chamadas de LLM ficam em cache: rodar de novo sem mudar prompt nem pergunta não gasta cota, e uma rodada
interrompida pelo limite diário continua de onde parou. Nada é gravado no banco.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

import yaml
from dotenv import load_dotenv
from groq import RateLimitError
from pydantic import BaseModel, Field

EVAL_DIR = pathlib.Path(__file__).resolve().parent
DEV_PATH = EVAL_DIR / "perguntas.yaml"
FINAL_PATH = EVAL_DIR / "perguntas-reservadas.yaml"
REPORT_PATH = EVAL_DIR / "report.md"
RETRIEVAL_REPORT_PATH = EVAL_DIR / "report-recuperacao.md"
CACHE_PATH = EVAL_DIR / ".cache" / "llm.json"

FAQ_K = 5
DOCS_K = 8
ANSWER_ROUTES = {"faq", "docs", "contatos", "eureca"}
CATALOG_SOURCES = {"eureca-horarios", "eureca-calendario", "pessoas.csv"}  # vêm do catálogo, não da busca

Outcome = Literal["resposta", "encaminhar", "recusar", "perguntar_qual", "conversa"]


class Question(BaseModel):
    id: str
    pergunta: str
    categoria: str
    esperado: list[Outcome]
    contem: list[str] = Field(default_factory=list)
    proibido: list[str] = Field(default_factory=list)
    fonte: str | None = None
    faq: str | None = None
    curriculo: str | None = None
    critica: bool = False
    rapido: bool = False
    origem: str = "autor"


@dataclass
class Score:
    question: Question
    outcome: str
    verdict: str  # correto | errado_com_seguranca | encaminhou_indevido | recusa_indevida | fonte_errada | outro_erro | erro_tecnico
    answer: str = ""
    problems: list[str] = field(default_factory=list)
    tokens: int = 0
    llm_calls: int = 0
    latency: float | None = None  # None quando alguma chamada veio do cache

    @property
    def correct(self) -> bool:
        return self.verdict == "correto"

    @property
    def harmful(self) -> bool:
        return self.verdict in ("errado_com_seguranca", "recusa_indevida") or (self.verdict == "fonte_errada" and self.question.critica)


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", plain)


def load_questions(path: pathlib.Path) -> list[Question]:
    questions = [Question(**item) for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []]
    repeated = [q for q, n in Counter(q.id for q in questions).items() if n > 1]
    if repeated:
        raise ValueError(f"IDs repetidos em {path.name}: {repeated}")
    return questions


def outcome_of(result: dict) -> str:
    route = result.get("route")
    if route in ANSWER_ROUTES:
        return "resposta"
    return {"escalated": "encaminhar", "out_of_scope": "recusar", "conversa": "conversa", "ambiguo": "perguntar_qual"}.get(route, "outro")


def content_problems(question: Question, answer: str) -> list[str]:
    text = normalize(answer)
    problems = [f"faltou: {item}" for item in question.contem if not any(normalize(a) in text for a in item.split("|"))]
    problems += [f"proibido: {item}" for item in question.proibido if normalize(item) in text]
    return problems


def judge(question: Question, result: dict) -> Score:
    outcome = outcome_of(result)
    answer = result.get("answer") or ""
    sources = result.get("sources") or []

    # Texto proibido vale para qualquer resultado.
    forbidden = [p for p in content_problems(question, answer) if p.startswith("proibido")]
    if outcome not in question.esperado:
        if outcome == "resposta":
            verdict = "errado_com_seguranca"
        elif outcome == "encaminhar":
            verdict = "encaminhou_indevido"
        elif outcome == "recusar":
            verdict = "recusa_indevida"
        else:
            verdict = "outro_erro"
        return Score(question, outcome, verdict, answer, [f"esperado {question.esperado}, obtido {outcome}", *forbidden])
    if outcome != "resposta":
        return Score(question, outcome, "errado_com_seguranca" if forbidden else "correto", answer, forbidden)

    problems = content_problems(question, answer)
    if problems:
        return Score(question, outcome, "errado_com_seguranca", answer, problems)
    if question.fonte and not any(question.fonte.lower() in s["doc"].lower() for s in sources):
        cited = [s["doc"] for s in sources]
        return Score(question, outcome, "fonte_errada", answer, [f"fonte esperada: {question.fonte}; citadas: {cited}"])
    return Score(question, outcome, "correto", answer)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def rate(hits: int, total: int) -> str:
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "sem perguntas"


def summarize(scores: list[Score]) -> dict:
    executed = [s for s in scores if s.verdict != "nao_executada"]
    answerable = [s for s in executed if "resposta" in s.question.esperado]
    outside = [s for s in executed if s.question.esperado == ["encaminhar"]]
    in_scope = [s for s in executed if "recusar" not in s.question.esperado]
    return {
        "executadas": len(executed),
        "corretas": sum(s.correct for s in executed),
        "criticos": [s for s in executed if s.question.critica and s.harmful],
        "errado_com_seguranca": sum(s.verdict == "errado_com_seguranca" for s in executed),
        "acerto_respondiveis": (sum(s.correct for s in answerable), len(answerable)),
        "encaminhamento_correto": (sum(s.correct for s in outside), len(outside)),
        "recusa_indevida": (sum(s.verdict == "recusa_indevida" for s in in_scope), len(in_scope)),
        "erros_tecnicos": sum(s.verdict == "erro_tecnico" for s in executed),
        "tokens": [s.tokens for s in executed if s.tokens],
        "chamadas": [s.llm_calls for s in executed if s.llm_calls],
        "latencias": [s.latency for s in executed if s.latency is not None],
    }


def render_report(scores: list[Score], title: str, partial: bool) -> str:
    data = summarize(scores)
    lines = [f"# {title}", "", f"Gerado em {dt.datetime.now():%Y-%m-%d %H:%M}. Modelo: `{os.getenv('LLM_MODEL')}`.", ""]
    if partial:
        missing = sum(s.verdict == "nao_executada" for s in scores)
        lines += [f"**Rodada parcial:** {missing} perguntas não foram executadas por limite de cota. Rode de novo para continuar.", ""]
    tokens, latencies = data["tokens"], data["latencias"]
    total = data["executadas"]
    lines += [
        "| Métrica | Resultado | Meta |",
        "|---|---|---|",
        f"| Erro em pergunta crítica | {len(data['criticos'])} | 0 |",
        f"| Resposta errada dada com segurança | {rate(data['errado_com_seguranca'], total)} | ≤ 3% |",
        f"| Acerto nas perguntas que a base responde | {rate(*data['acerto_respondiveis'])} | ≥ 85% |",
        f"| Encaminhamento correto do que está fora da base | {rate(*data['encaminhamento_correto'])} | ≥ 90% |",
        f"| Recusa indevida de tema do curso | {rate(*data['recusa_indevida'])} | ≤ 2% |",
        f"| Erros técnicos | {data['erros_tecnicos']} | 0 |",
        f"| Latência p95 (só sem cache, {len(latencies)} perguntas) | {percentile(latencies, 0.95) or 0:.1f} s | ≤ 15 s |",
        f"| Tokens por pergunta (média / p95) | {statistics.mean(tokens) if tokens else 0:.0f} / {percentile(tokens, 0.95) or 0} | medir |",
        f"| Chamadas de LLM por pergunta (média) | {statistics.mean(data['chamadas']) if data['chamadas'] else 0:.1f} | medir |",
        "",
        "## Por categoria",
        "",
        "| Categoria | Acerto |",
        "|---|---|",
    ]
    by_category = {}
    for score in scores:
        if score.verdict != "nao_executada":
            by_category.setdefault(score.question.categoria, []).append(score.correct)
    lines += [f"| {name} | {rate(sum(v), len(v))} |" for name, v in sorted(by_category.items())]
    lines += ["", "## Por origem", "", "| Origem | Acerto |", "|---|---|"]
    by_origin = {}
    for score in scores:
        if score.verdict != "nao_executada":
            by_origin.setdefault(score.question.origem, []).append(score.correct)
    lines += [f"| {name} | {rate(sum(v), len(v))} |" for name, v in sorted(by_origin.items())]
    lines += ["", "## Resultado por tipo", "", "| Tipo | Quantidade |", "|---|---|"]
    lines += [f"| {name} | {n} |" for name, n in Counter(s.verdict for s in scores).most_common()]

    failures = [s for s in scores if not s.correct and s.verdict != "nao_executada"]
    failures.sort(key=lambda s: (not s.question.critica, s.verdict))
    lines += ["", "## Falhas", ""]
    for score in failures:
        mark = " **(crítica)**" if score.question.critica else ""
        lines.append(f"- `{score.question.id}`{mark} {score.verdict}: \"{score.question.pergunta}\"")
        lines += [f"  - {problem}" for problem in score.problems]
        if score.answer:
            excerpt = score.answer.replace("\n", " ")[:200]
            lines.append(f"  - bot: {excerpt}")
    return "\n".join(lines) + "\n"


class LlmCache:
    """Guarda as respostas do LLM em disco. A chave inclui modelo, schema, prompt e entrada."""

    def __init__(self, path: pathlib.Path = CACHE_PATH):
        self.path = path
        self.entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def key(self, schema: type[BaseModel], system: str, user: str) -> str:
        raw = json.dumps([os.getenv("LLM_MODEL"), schema.__name__, schema.model_json_schema(), system, user], sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, value: dict) -> None:
        self.entries[key] = value
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.entries, ensure_ascii=False), encoding="utf-8")


@dataclass
class CallMeter:
    tokens: int = 0
    calls: int = 0
    cached: int = 0


def cached_ask(real_ask, usage_log, cache: LlmCache, meter: CallMeter):
    """Envolve llm.ask: reaproveita respostas guardadas e conta tokens, inclusive os das respostas do cache."""

    def ask(schema: type[BaseModel], system: str, user: str) -> BaseModel:
        key = cache.key(schema, system, user)
        hit = cache.get(key)
        if hit:
            meter.tokens += hit["tokens"]
            meter.calls += 1
            meter.cached += 1
            return schema(**hit["parsed"])
        before = len(usage_log)
        result = real_ask(schema, system, user)
        tokens = sum(entry["total_tokens"] or 0 for entry in list(usage_log)[before:])
        cache.put(key, {"parsed": result.model_dump(), "tokens": tokens})
        meter.tokens += tokens
        meter.calls += 1
        return result

    return ask


def run_full(questions: list[Question], pause: float) -> tuple[list[Score], bool]:
    from app.agentes import grafo as graph
    from app.base import llm

    meter = CallMeter()
    graph.ask = cached_ask(llm.ask, llm.usage_log, LlmCache(), meter)

    def registrar(state: dict) -> dict:  # a avaliação não grava conversas
        return {}

    graph.registrar = registrar
    graph.get_graph.cache_clear()

    scores, stopped = [], False
    for number, question in enumerate(questions, start=1):
        if stopped:
            scores.append(Score(question, "", "nao_executada"))
            continue
        meter.tokens, meter.calls, meter.cached = 0, 0, 0
        start = time.perf_counter()
        try:
            result = graph.answer_question(question.pergunta, curriculum=question.curriculo, channel="eval")
            score = judge(question, result)
        except RateLimitError:
            print(f"Limite de cota na pergunta {question.id}. As próximas ficam para a próxima rodada.", flush=True)
            scores.append(Score(question, "", "nao_executada"))
            stopped = True
            continue
        except Exception as error:
            score = Score(question, "erro", "erro_tecnico", problems=[f"{type(error).__name__}: {str(error)[:200]}"])
        score.tokens, score.llm_calls = meter.tokens, meter.calls
        score.latency = (time.perf_counter() - start) if not meter.cached else None
        scores.append(score)
        print(f"[{number}/{len(questions)}] {question.id} {score.verdict} ({meter.tokens} tokens)", flush=True)
        if pause and meter.cached < meter.calls:
            time.sleep(pause)
    return scores, stopped


def articles_in(excerpt: str) -> set[int]:
    """Números de artigo citados na fonte do FAQ: "Art. 166 §1º, Arts. 168 e 169", "Arts. 270 a 279"."""
    if "art" not in excerpt.lower():
        return set()
    text = re.sub(r"§\s*\d+º?", "", excerpt)
    numbers = {int(n) for n in re.findall(r"\d+", text)}
    for first, last in re.findall(r"(\d+)º?\s+a\s+(\d+)", text):
        numbers |= set(range(int(first), int(last) + 1))
    return numbers


def faq_cases() -> list[dict]:
    """Casos de busca sem custo: cada pergunta do FAQ deve encontrar o documento e o artigo que a entrada cita."""
    from app.base.config import DATA_DIR
    from app.conhecimento.manifest import load_all_faq, load_manifest

    searchable = {d.arquivo for d in load_manifest(DATA_DIR / "manifest.yaml") if d.ingerir}
    cases = []
    for entry in load_all_faq(DATA_DIR):
        sources = [s for s in entry.fontes if s.doc in searchable]
        if sources:
            articles = set().union(*(articles_in(s.trecho) for s in sources))
            for text in [entry.pergunta, *entry.variacoes]:
                cases.append({"id": entry.id, "pergunta": text, "docs": {s.doc for s in sources}, "artigos": articles})
    return cases


def rank_metrics(ranks: list[int | None]) -> str:
    found = [rank for rank in ranks if rank]
    mrr = sum(1 / rank for rank in found) / len(ranks) if ranks else 0
    return f"{rate(len(found), len(ranks))}, primeiro lugar em {sum(r == 1 for r in found)}, MRR {mrr:.2f}"


def first_rank(items: list, matches) -> int | None:
    return next((position for position, item in enumerate(items, start=1) if matches(item)), None)


def run_retrieval(questions: list[Question], mode: str) -> str:
    from app.conhecimento.busca import search_docs, search_faq
    from app.conhecimento.catalogo import find_discipline_mentions

    faq_ranks, doc_ranks, in_scores, out_scores, misses = [], [], [], [], []
    started = time.perf_counter()
    searches = 0
    for question in questions:
        faq_hits = search_faq(question.pergunta, k=FAQ_K)
        doc_hits = search_docs(question.pergunta, k=DOCS_K, mode=mode)
        searches += 1
        if question.esperado == ["encaminhar"]:
            out_scores.append(doc_hits[0]["similarity"])
        if question.faq:
            ids = [hit["id"] for hit in faq_hits]
            rank = ids.index(question.faq) + 1 if question.faq in ids else None
            faq_ranks.append(rank)
            in_scores.append(faq_hits[0]["similarity"])
            if rank is None:
                misses.append(f"- `{question.id}` FAQ `{question.faq}` fora do top {FAQ_K}: \"{question.pergunta}\" (veio: {ids[:3]})")
        # Dados de uma disciplina citada vêm do catálogo; a busca só precisa achar disciplina por assunto.
        by_catalog = question.fonte == "disciplinas.json" and find_discipline_mentions(question.pergunta)
        if question.fonte and question.fonte not in CATALOG_SOURCES and not by_catalog:
            docs = [hit["document_id"] for hit in doc_hits]
            rank = first_rank(docs, lambda doc: question.fonte.lower() in doc.lower())
            doc_ranks.append(rank)
            if rank is None:
                misses.append(f"- `{question.id}` documento `{question.fonte}` fora do top {DOCS_K}: \"{question.pergunta}\" (veio: {docs[:3]})")

    source_ranks, article_ranks = [], []
    for case in faq_cases():
        hits = search_docs(case["pergunta"], k=DOCS_K, mode=mode)
        searches += 1
        source_ranks.append(first_rank(hits, lambda hit: hit["document_id"] in case["docs"]))
        if case["artigos"]:
            wanted = {f"Art. {number}" for number in case["artigos"]}
            article_ranks.append(first_rank(hits, lambda hit: hit["locator"] in wanted))
            if article_ranks[-1] is None:
                misses.append(f"- FAQ `{case['id']}` artigos {sorted(case['artigos'])[:4]} fora do top {DOCS_K}: \"{case['pergunta']}\"")
    elapsed = (time.perf_counter() - started) / searches

    def spread(values: list[float]) -> str:
        return f"mín {min(values):.3f}, mediana {statistics.median(values):.3f}, máx {max(values):.3f}" if values else "sem dados"

    lines = [
        "# Avaliação da recuperação",
        "",
        f"Gerado em {dt.datetime.now():%Y-%m-%d %H:%M}. Sem LLM. Modo de busca: `{mode}`.",
        "",
        f"- FAQ correto no top {FAQ_K}: {rank_metrics(faq_ranks)}",
        f"- Documento correto no top {DOCS_K} (perguntas de avaliação): {rank_metrics(doc_ranks)}",
        f"- Documento citado pelo FAQ no top {DOCS_K}: {rank_metrics(source_ranks)}",
        f"- Artigo citado pelo FAQ no top {DOCS_K}: {rank_metrics(article_ranks)}",
        f"- Maior nota do FAQ, perguntas dentro da base: {spread(in_scores)}",
        f"- Maior nota dos documentos, perguntas fora da base: {spread(out_scores)}",
        f"- Tempo médio por busca de documentos e embeddings: {elapsed:.2f} s",
        "",
        "## Falhas",
        "",
        *(misses or ["Nenhuma."]),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--recuperacao", action="store_true", help="avalia só a busca, sem LLM")
    mode.add_argument("--rapido", action="store_true", help="fluxo completo nas perguntas marcadas com rapido")
    parser.add_argument("--final", action="store_true", help="usa as perguntas reservadas")
    parser.add_argument("--busca", default="vetorial", help="modo de busca na avaliação da recuperação")
    parser.add_argument("--pausa", type=float, default=5.0, help="segundos entre perguntas, para respeitar o limite por minuto")
    args = parser.parse_args()

    load_dotenv()
    questions = load_questions(FINAL_PATH if args.final else DEV_PATH)
    if not questions:
        print(f"Nenhuma pergunta em {(FINAL_PATH if args.final else DEV_PATH).name}.")
        return 1

    if args.recuperacao:
        RETRIEVAL_REPORT_PATH.write_text(run_retrieval(questions, args.busca), encoding="utf-8")
        print(f"Relatório: {RETRIEVAL_REPORT_PATH}")
        return 0

    if args.rapido:
        questions = [q for q in questions if q.rapido]
    scores, partial = run_full(questions, args.pausa)
    label = "reservado" if args.final else "rápido" if args.rapido else "desenvolvimento"
    REPORT_PATH.write_text(render_report(scores, f"Avaliação do bot: conjunto {label}", partial), encoding="utf-8")
    print(f"Relatório: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
