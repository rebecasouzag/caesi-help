"""Cliente da API pública do Eureca, restrito ao curso de Ciência da Computação de Campina Grande.

A API cobre todos os cursos da UFCG e tem rotas sobre estudantes. Aqui só existem as consultas que o bot pode fazer,
e todas já levam o código do curso: quem usa este módulo não consegue pedir dados de outro curso nem de pessoas.
O filtro por curso não basta sozinho: ele também devolve turmas de outros cursos (ver sync_eureca.only_course_disciplines).
"""
import pathlib

import httpx
import yaml

from app.base.config import DATA_DIR

BASE_URL = "https://eureca.sti.ufcg.edu.br/das-sig/v1"
COURSE = 108095
CAMPUS = 1
TIMEOUT_SECONDS = 60
CONFIRMED_CALENDARS_PATH = DATA_DIR / "catalogo" / "calendarios_confirmados.yaml"


class AmbiguousCalendar(Exception):
    """A API tem mais de um calendário para o período, e nenhum foi confirmado pelo CA."""


def _get(route: str, params: dict, client: httpx.Client | None):
    """Faz o GET e devolve o JSON. A API responde 404 quando a consulta não tem resultados."""
    get = client.get if client else httpx.get
    response = get(f"{BASE_URL}{route}", params=params, headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


def calendars(client: httpx.Client | None = None) -> list[dict]:
    """O calendário é do campus, não de um curso. A rota também devolve períodos passados."""
    return _get("/calendarios/periodo-corrente", {"campus": CAMPUS}, client)


def current_period(client: httpx.Client | None = None) -> str:
    return max(row["periodo"] for row in calendars(client))


def load_confirmed_calendars(path: pathlib.Path = CONFIRMED_CALENDARS_PATH) -> dict[str, int]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def choose_calendar(rows: list[dict], confirmed: dict[str, int] | None = None) -> dict:
    """Calendário do período atual. A API pode repetir um período com datas diferentes, e nem o mais novo é
    garantidamente o certo: nesse caso, vale o registro confirmado pelo CA. Sem confirmação, não escolhe."""
    period = max(row["periodo"] for row in rows)
    candidates = [row for row in rows if row["periodo"] == period]
    if len(candidates) == 1:
        return candidates[0]
    confirmed_id = (load_confirmed_calendars() if confirmed is None else confirmed).get(period)
    for row in candidates:
        if row["id"] == confirmed_id:
            return row
    raise AmbiguousCalendar(f"O período {period} tem {len(candidates)} calendários e nenhum confirmado: {[r['id'] for r in candidates]}")


def current_calendar(client: httpx.Client | None = None, confirmed: dict[str, int] | None = None) -> dict:
    return choose_calendar(calendars(client), confirmed)


def curriculum_disciplines(curriculum: str, client: httpx.Client | None = None) -> list[dict]:
    return _get("/disciplinas-por-curriculo", {"curso": COURSE, "curriculo": curriculum}, client)


def classes(period: str, client: httpx.Client | None = None) -> list[dict]:
    return _get("/turmas", {"curso": COURSE, "periodo-de": period, "periodo-ate": period}, client)


def schedules(period: str, client: httpx.Client | None = None) -> list[dict]:
    return _get("/horarios", {"curso": COURSE, "periodo-de": period, "periodo-ate": period}, client)
