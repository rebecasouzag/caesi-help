import pytest
from pydantic import BaseModel

from eval.run_eval import (
    DEV_PATH,
    FINAL_PATH,
    CallMeter,
    LlmCache,
    Question,
    Score,
    articles_in,
    cached_ask,
    judge,
    load_questions,
    outcome_of,
    render_report,
    summarize,
)


def question(**fields):
    base = {"id": "q", "pergunta": "?", "categoria": "c", "esperado": ["resposta"]}
    return Question(**{**base, **fields})


def faq_result(answer="Texto", sources=()):
    return {"route": "faq", "answer": answer, "sources": list(sources)}


@pytest.mark.parametrize("result, expected", [
    ({"route": "faq"}, "resposta"),
    ({"route": "docs"}, "resposta"),
    ({"route": "contatos"}, "resposta"),
    ({"route": "escalated"}, "encaminhar"),
    ({"route": "out_of_scope"}, "recusar"),
])
def test_resultado_vem_da_rota(result, expected):
    assert outcome_of(result) == expected


def test_resposta_certa_ignora_acento_e_maiuscula():
    score = judge(question(contem=["matrícula"]), faq_result("MATRICULA no SIGAA"))
    assert score.correct


def test_barra_aceita_alternativas():
    assert judge(question(contem=["laudo|atestado"]), faq_result("com atestado")).correct


def test_faltar_conteudo_e_erro_com_seguranca():
    score = judge(question(contem=["80 créditos"]), faq_result("Pode sim."))
    assert score.verdict == "errado_com_seguranca"
    assert score.harmful


def test_texto_proibido_e_erro_com_seguranca():
    score = judge(question(proibido=["14 créditos"]), faq_result("O mínimo é 14 creditos."))
    assert score.verdict == "errado_com_seguranca"


def test_responder_o_que_devia_encaminhar_e_erro_com_seguranca():
    score = judge(question(esperado=["encaminhar"]), faq_result())
    assert score.verdict == "errado_com_seguranca"


def test_encaminhar_o_que_devia_responder_nao_e_grave():
    score = judge(question(), {"route": "escalated", "answer": "Encaminhei"})
    assert score.verdict == "encaminhou_indevido"
    assert not score.harmful


def test_recusar_tema_do_curso_e_grave():
    score = judge(question(), {"route": "out_of_scope", "answer": "Não ajudo"})
    assert score.verdict == "recusa_indevida"
    assert score.harmful


def test_conteudo_nao_e_cobrado_de_quem_encaminhou_corretamente():
    score = judge(question(esperado=["resposta", "encaminhar"], contem=["75"]), {"route": "escalated", "answer": "Encaminhei"})
    assert score.correct


def test_fonte_errada_so_e_grave_em_pergunta_critica():
    sources = [{"doc": "outro.pdf", "trecho": "p. 1"}]
    assert judge(question(fonte="res_11"), faq_result(sources=sources)).verdict == "fonte_errada"
    assert not judge(question(fonte="res_11"), faq_result(sources=sources)).harmful
    assert judge(question(fonte="res_11", critica=True), faq_result(sources=sources)).harmful


def test_fonte_certa_ignora_maiuscula():
    sources = [{"doc": "RES_09.2025_-_MONITORIA.pdf", "trecho": "Art. 1"}]
    assert judge(question(fonte="monitoria"), faq_result(sources=sources)).correct


def test_resumo_conta_as_metricas_e_ignora_nao_executadas():
    scores = [
        Score(question(id="a", critica=True), "resposta", "errado_com_seguranca", tokens=100, llm_calls=2),
        Score(question(id="b"), "resposta", "correto", tokens=300, llm_calls=4, latency=2.0),
        Score(question(id="c", esperado=["encaminhar"]), "encaminhar", "correto"),
        Score(question(id="d"), "", "nao_executada"),
    ]
    data = summarize(scores)
    assert data["executadas"] == 3
    assert [s.question.id for s in data["criticos"]] == ["a"]
    assert data["acerto_respondiveis"] == (1, 2)
    assert data["encaminhamento_correto"] == (1, 1)
    assert data["tokens"] == [100, 300]


def test_relatorio_avisa_rodada_parcial():
    scores = [Score(question(), "resposta", "correto"), Score(question(id="b"), "", "nao_executada")]
    report = render_report(scores, "Título", partial=True)
    assert "Rodada parcial" in report
    assert "1 perguntas não foram executadas" in report


class Answer(BaseModel):
    text: str


def test_cache_nao_chama_o_llm_de_novo_e_conta_os_tokens(tmp_path):
    usage_log = []
    calls = []

    def real_ask(schema, system, user):
        calls.append(user)
        usage_log.append({"total_tokens": 42})
        return schema(text="oi")

    meter = CallMeter()
    ask = cached_ask(real_ask, usage_log, LlmCache(tmp_path / "cache" / "llm.json"), meter)
    assert ask(Answer, "s", "u").text == "oi"
    assert ask(Answer, "s", "u").text == "oi"
    assert calls == ["u"]
    assert (meter.tokens, meter.calls, meter.cached) == (84, 2, 1)


def test_cache_sobrevive_a_outra_rodada_e_muda_com_o_prompt(tmp_path):
    path = tmp_path / "cache" / "llm.json"
    calls = []

    def real_ask(schema, system, user):
        calls.append(system)
        return schema(text="oi")

    cached_ask(real_ask, [], LlmCache(path), CallMeter())(Answer, "s1", "u")
    second = cached_ask(real_ask, [], LlmCache(path), CallMeter())
    second(Answer, "s1", "u")
    assert calls == ["s1"]
    second(Answer, "s2", "u")
    assert calls == ["s1", "s2"]


@pytest.mark.parametrize("excerpt, expected", [
    ("Art. 166 §1º, Arts. 168 e 169", {166, 168, 169}),
    ("Arts. 270 a 273", {270, 271, 272, 273}),
    ("Arts. 6º, 18 e 25", {6, 18, 25}),
    ("Anexo I, itens 4 e 7", set()),
    ("Trancamentos", set()),
])
def test_artigos_citados_na_fonte_do_faq(excerpt, expected):
    assert articles_in(excerpt) == expected


def test_conjuntos_de_perguntas_sao_validos():
    questions = load_questions(DEV_PATH)
    assert len(questions) >= 100
    assert load_questions(FINAL_PATH) == []
    assert {q.id for q in questions if q.rapido} and any(q.critica for q in questions)
