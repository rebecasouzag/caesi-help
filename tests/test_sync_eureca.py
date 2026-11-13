import json

import httpx
import pytest

from app.base import eureca
from app.ingestao import sync_eureca


class FakeClient:
    """Guarda as consultas e responde conforme a rota."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        route = url.removeprefix(eureca.BASE_URL)
        self.calls.append((route, params))
        request = httpx.Request("GET", url)
        answer = self.responses.get(route, [])
        return httpx.Response(answer, request=request) if isinstance(answer, int) else httpx.Response(200, json=answer, request=request)


def test_consultas_sempre_levam_o_curso_de_computacao():
    client = FakeClient({})
    eureca.curriculum_disciplines("2023", client)
    eureca.classes("2026.2", client)
    eureca.schedules("2026.2", client)
    assert all(params["curso"] == 108095 for _, params in client.calls)


REPEATED_PERIOD = {"/calendarios/periodo-corrente": [
    {"id": 3, "periodo": "2025.2", "inicio_das_aulas": "2025-10-16"},
    {"id": 1, "periodo": "2026.2", "inicio_das_aulas": "2026-09-08"},
    {"id": 2, "periodo": "2026.2", "inicio_das_aulas": "2026-10-09"},
]}


def test_periodo_atual_nao_depende_do_calendario_escolhido():
    assert eureca.current_period(FakeClient(REPEATED_PERIOD)) == "2026.2"


def test_periodo_repetido_usa_o_calendario_confirmado_mesmo_sendo_o_mais_antigo():
    """Caso real: em 2026.2, o registro certo é o de menor id."""
    calendar = eureca.current_calendar(FakeClient(REPEATED_PERIOD), confirmed={"2026.2": 1})
    assert calendar["inicio_das_aulas"] == "2026-09-08"


def test_periodo_repetido_sem_confirmacao_nao_escolhe_uma_data():
    with pytest.raises(eureca.AmbiguousCalendar):
        eureca.current_calendar(FakeClient(REPEATED_PERIOD), confirmed={})


def test_periodo_sem_repeticao_nao_precisa_de_confirmacao():
    client = FakeClient({"/calendarios/periodo-corrente": [{"id": 9, "periodo": "2027.1"}, {"id": 3, "periodo": "2026.2"}]})
    assert eureca.current_calendar(client, confirmed={})["id"] == 9


def test_calendario_confirmado_de_2026_2_e_o_que_o_ca_indicou():
    assert eureca.load_confirmed_calendars()["2026.2"] == 11426251


def test_modulo_nao_oferece_rotas_de_pessoas():
    public = {name for name in vars(eureca) if not name.startswith("_")}
    assert not {name for name in public if "estudante" in name or "matricula" in name or "professor" in name}


def test_404_da_api_significa_sem_resultados():
    assert eureca.classes("2026.2", FakeClient({"/turmas": 404})) == []


def test_erro_do_servidor_nao_vira_lista_vazia():
    with pytest.raises(httpx.HTTPStatusError):
        eureca.classes("2026.2", FakeClient({"/turmas": 500}))


def test_select_guarda_so_os_campos_em_ordem_estavel():
    rows = [{"a": 2, "extra": "x"}, {"a": 1, "extra": "y"}]
    assert sync_eureca.select(rows, ("a",)) == [{"a": 1}, {"a": 2}]


def test_disciplina_de_outro_curso_com_o_mesmo_nome_e_descartada():
    """Caso real: o filtro por curso devolve Inglês 1307011, mas o do currículo é 1307150."""
    client = FakeClient({
        "/disciplinas-por-curriculo": [{"codigo_da_disciplina": 1307150, "nome": "INGLÊS"}],
        "/turmas": [{"codigo_da_disciplina": 1307150, "turma": "01"}, {"codigo_da_disciplina": 1307011, "turma": "01"}],
        "/horarios": [
            {"codigo_da_disciplina": 1307150, "nome_da_disciplina": "INGLÊS", "turma": "01"},
            {"codigo_da_disciplina": 1307011, "nome_da_disciplina": "INGLÊS", "turma": "01"},
        ],
    })
    files, dropped = sync_eureca.snapshot_files("2026.2", client)
    assert [row["codigo_da_disciplina"] for row in files["turmas-2026.2.json"]] == [1307150]
    assert [row["codigo_da_disciplina"] for row in files["horarios-2026.2.json"]] == [1307150]
    assert dropped == {1307011: "INGLÊS"}


def test_snapshot_junta_os_dois_curriculos():
    client = FakeClient({"/disciplinas-por-curriculo": [{"codigo_da_disciplina": 1}]})
    files, _ = sync_eureca.snapshot_files("2026.2", client)
    assert set(files) == {"curriculo-2017.json", "curriculo-2023.json", "turmas-2026.2.json", "horarios-2026.2.json"}
    assert [params["curriculo"] for route, params in client.calls if route == "/disciplinas-por-curriculo"] == ["2017", "2023"]


def test_resultado_vazio_nao_apaga_a_copia_anterior(tmp_path):
    path = tmp_path / "turmas.json"
    path.write_text('[{"antigo": 1}]', encoding="utf-8")
    report = sync_eureca.write_files({"turmas.json": []}, tmp_path)
    assert report["empty"] == ["turmas.json"]
    assert json.loads(path.read_text(encoding="utf-8")) == [{"antigo": 1}]


def test_grava_so_o_que_mudou(tmp_path):
    files = {"a.json": [{"x": 1}]}
    assert sync_eureca.write_files(files, tmp_path)["changed"] == ["a.json"]
    assert sync_eureca.write_files(files, tmp_path)["unchanged"] == ["a.json"]
    assert sync_eureca.write_files({"a.json": [{"x": 2}]}, tmp_path)["changed"] == ["a.json"]
