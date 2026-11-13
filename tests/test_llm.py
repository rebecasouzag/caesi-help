import httpx
import pytest
from groq import BadRequestError, RateLimitError
from pydantic import BaseModel

from app.base import llm
from app.agentes.grafo import Classification


class Answer(BaseModel):
    text: str


class FakeChain:
    """Devolve ou levanta cada item, na ordem."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeRaw:
    usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def ok(text="oi"):
    return {"raw": FakeRaw(), "parsed": Answer(text=text), "parsing_error": None}


def invalid():
    return {"raw": FakeRaw(), "parsed": None, "parsing_error": "schema"}


def rate_limit(retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, headers=headers, request=httpx.Request("POST", "https://groq"))
    return RateLimitError("limite", response=response, body=None)


def bad_request(message):
    response = httpx.Response(400, request=httpx.Request("POST", "https://groq"))
    return BadRequestError(message, response=response, body=None)


@pytest.fixture
def run(monkeypatch):
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    llm.usage_log.clear()

    def _run(*outcomes):
        chain = FakeChain(outcomes)
        fake = type("Llm", (), {"with_structured_output": lambda self, schema, include_raw: chain})()
        monkeypatch.setattr(llm, "get_llm", lambda: fake)
        return chain, waits

    return _run


def test_resposta_valida_registra_tokens(run):
    run(ok())
    assert llm.ask(Answer, "s", "u").text == "oi"
    assert llm.usage_log[-1] == {"schema": "Answer", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_saida_recusada_pelo_provedor_tenta_de_novo(run):
    chain, _ = run(bad_request("tool_use_failed: categoria nula"), ok())
    assert llm.ask(Answer, "s", "u").text == "oi"
    assert chain.calls == 2


def test_outro_erro_400_nao_tenta_de_novo(run):
    chain, _ = run(bad_request("modelo inexistente"), ok())
    with pytest.raises(BadRequestError):
        llm.ask(Answer, "s", "u")
    assert chain.calls == 1


def test_limite_por_minuto_espera_o_tempo_indicado(run):
    _, waits = run(rate_limit("7"), ok())
    assert llm.ask(Answer, "s", "u").text == "oi"
    assert waits == [7.0]


def test_limite_diario_falha_sem_esperar(run):
    chain, waits = run(rate_limit("3600"), ok())
    with pytest.raises(RateLimitError):
        llm.ask(Answer, "s", "u")
    assert chain.calls == 1
    assert waits == []


def test_saida_invalida_tenta_de_novo_e_desiste(run):
    chain, _ = run(invalid(), invalid(), invalid())
    with pytest.raises(ValueError):
        llm.ask(Answer, "s", "u")
    assert chain.calls == llm.MAX_ATTEMPTS


def test_categoria_nula_nao_derruba_a_classificacao():
    assert Classification(intent="faq_docs", categoria=None).categoria is None
    assert Classification(intent="faq_docs").categoria is None
