"""Calendário acadêmico (Eureca) e leitura do código de horário do SIGAA."""
import datetime as dt
import json
import re
import time

import httpx

from app.base import eureca
from app.conhecimento.catalogo import EURECA_DIR, WEEKDAYS
from app.ferramentas.evidencia import Evidencia, Resultado

CALENDAR_DOC = "eureca-calendario"
CALENDAR_TITLE = "Calendário acadêmico no Eureca"
OFFICIAL_CALENDAR_URL = "https://portal.pre.ufcg.edu.br/calendarios"
SNAPSHOT_PATH = EURECA_DIR / "calendarios.json"
CACHE_SECONDS = 24 * 60 * 60
SIGAA_DOC = "historico-grupo.md"
SIGAA_TITLE = "Relatos de alunos no grupo do curso (não oficial)"
SIGAA_CODE_RE = re.compile(r"\b([2-7]+)([MTN])([1-7]+)\b", re.IGNORECASE)
# Hora em que o primeiro horário de cada turno começa: M2 = 8h, M4 = 10h, T2 = 14h, T4 = 16h.
SHIFT_START = {"M": 7, "T": 13}

_cache: tuple[float, dict] | None = None


def format_date(value: str) -> str:
    return dt.date.fromisoformat(value).strftime("%d/%m/%Y")


def calendar_evidence(row: dict, stale: bool = False) -> Evidencia:
    lines = [
        f"Período {row['periodo']}.",
        f"Início das matrículas: {format_date(row['inicio_das_matriculas'])}.",
        f"Início das aulas: {format_date(row['inicio_das_aulas'])}.",
        f"1/4 do período: {format_date(row['um_quarto_do_periodo'])}.",
        f"1/3 do período: {format_date(row['um_terco_do_periodo'])}.",
        f"Último dia para registro de notas: {format_date(row['ultimo_dia_para_registro_de_notas'])}.",
    ]
    if stale:
        lines.append("O Eureca não respondeu: estas datas vêm da última cópia guardada e podem estar desatualizadas.")
    return Evidencia(f"{CALENDAR_DOC}:{row['periodo']}", "\n".join(lines), CALENDAR_DOC, CALENDAR_TITLE, f"Período {row['periodo']}", url=OFFICIAL_CALENDAR_URL)


def unavailable(reason: str) -> Resultado:
    return Resultado("indisponivel", mensagem=f"{reason} Confira o calendário oficial em {OFFICIAL_CALENDAR_URL}.")


def calendario_atual(fetch=eureca.calendars, now=time.time) -> Resultado:
    """Datas do período atual. Com a API fora do ar, usa a última cópia; sem confirmação de qual calendário vale, não afirma data."""
    global _cache
    if _cache and now() - _cache[0] < CACHE_SECONDS:
        return Resultado("encontrado", [calendar_evidence(_cache[1])])
    try:
        row = eureca.choose_calendar(fetch())
    except eureca.AmbiguousCalendar:
        return unavailable("O Eureca tem mais de um calendário para o período, e nenhum foi confirmado.")
    except httpx.HTTPError:
        if not SNAPSHOT_PATH.exists():
            return unavailable("Não consegui consultar o Eureca agora.")
        try:
            return Resultado("encontrado", [calendar_evidence(eureca.choose_calendar(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))), stale=True)])
        except eureca.AmbiguousCalendar:
            return unavailable("Não consegui consultar o Eureca agora.")
    _cache = (now(), row)
    return Resultado("encontrado", [calendar_evidence(row)])


def reset_cache() -> None:
    global _cache
    _cache = None


def describe_slots(shift: str, slots: str) -> str | None:
    numbers = sorted(int(slot) for slot in slots)
    if shift not in SHIFT_START or numbers != list(range(numbers[0], numbers[-1] + 1)):
        return None
    start = SHIFT_START[shift] + numbers[0] - 1
    return f"das {start}h às {start + len(numbers)}h"


def decodificar_horario_sigaa(texto: str) -> Resultado:
    """Lê códigos como 24T23: dias da semana, turno e horários. O turno da noite não é interpretado."""
    lines = []
    for match in SIGAA_CODE_RE.finditer(texto):
        days, shift, slots = match.group(1), match.group(2).upper(), match.group(3)
        time_range = describe_slots(shift, slots)
        if not time_range or any(int(day) not in WEEKDAYS for day in days):
            continue
        names = [WEEKDAYS[int(day)] for day in days]
        joined = " e ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + f" e {names[-1]}"
        lines.append(f"{match.group(0).upper()} significa {joined}, {time_range}.")
    if not lines:
        return Resultado("nao_encontrado")
    text = "\n".join(lines) + "\nNo código, os primeiros números são os dias (2 = segunda ... 6 = sexta), a letra é o turno (M = manhã, T = tarde) e os últimos números são os horários do turno."
    return Resultado("encontrado", [Evidencia(f"sigaa:{lines[0]}", text, SIGAA_DOC, SIGAA_TITLE, "Código de horário do SIGAA", procedencia="comunidade")])
