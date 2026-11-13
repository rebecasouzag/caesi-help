"""Catálogo do curso: disciplinas, pessoas, requisitos e horário, ligados por um identificador comum.

Fontes: Eureca (grade, turmas, horários), PPC (ementa e requisitos), correções do CA e pessoas.csv.
O código da disciplina no Eureca liga tudo, porque os nomes variam de uma fonte para outra.
"""
import csv
import json
import pathlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.base.config import DATA_DIR
from app.conhecimento.entidades import Resolution, compact, normalize, resolve
from app.conhecimento.requisitos import rebuild_requirements

CATALOG_DIR = DATA_DIR / "catalogo"
EURECA_DIR = DATA_DIR / "eureca"
PPC_PATH = DATA_DIR / "raw" / "disciplinas.json"
CURRICULA = ("2017", "2023")
WEEKDAYS = {2: "segunda", 3: "terça", 4: "quarta", 5: "quinta", 6: "sexta", 7: "sábado"}
WEEKDAY_NUMBERS = {name: number for number, name in WEEKDAYS.items()}
LOWERCASE_WORDS = {"de", "da", "do", "dos", "das", "e", "em", "a", "o", "para", "na", "no", "à"}
UPPERCASE_WORDS = {"I", "II", "III", "IV", "V", "TECC", "TI", "IA"}
MAX_ALIAS_WORDS = 3
MIN_ALIAS_LENGTH = 3


@dataclass(frozen=True)
class Discipline:
    code: int
    name: str
    codes: tuple[int, ...]  # o mesmo nome pode ter um código em cada currículo
    curricula: tuple[str, ...]
    kind: str
    semester: int
    hours: int | None
    credits: int | None
    summary: str | None = None  # ementa; só existe para disciplinas do PPC


@dataclass(frozen=True)
class Meeting:
    code: int
    turma: str
    day: str | None
    start: int | None
    end: int | None
    room: str | None


@dataclass
class Catalog:
    period: str
    disciplines: dict[int, Discipline]
    discipline_names: dict[str, str]  # chave (código em texto) -> nome
    discipline_aliases: dict[str, str]
    people: dict[str, dict]
    people_names: dict[str, str]
    people_aliases: dict[str, str]
    requirements: dict[str, dict[str, list[str]]]  # nome do PPC -> {"pre": [...], "co": [...]}
    teachers: dict[tuple[int, str], list[str]] = field(default_factory=dict)  # (código, turma) -> ids de pessoas
    meetings: list[Meeting] = field(default_factory=list)


def title_case(name: str) -> str:
    """O Eureca traz os nomes em maiúsculas."""
    def fix(match: re.Match) -> str:
        word = match.group(0)
        if word in UPPERCASE_WORDS:
            return word
        lower = word.lower()
        return lower if lower in LOWERCASE_WORDS and match.start() > 0 else lower.capitalize()
    return re.sub(r"\w+", fix, name)


def read_csv(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_period() -> str:
    return max(path.stem.removeprefix("turmas-") for path in EURECA_DIR.glob("turmas-*.json"))


def load_requirements(ppc: dict[str, dict], code_of_ppc_name: dict[str, int], names_by_code: dict[int, str]) -> dict:
    requirements = {}
    for name, record in ppc.items():
        pre, co = rebuild_requirements(record["prerequisitos"], list(ppc))
        requirements[name] = {"pre": pre, "co": co}
    # Correções do CA têm prioridade sobre o PPC e são acrescentadas com o nome usado nele.
    for fix in yaml.safe_load((CATALOG_DIR / "correcoes_requisitos.yaml").read_text(encoding="utf-8")) or []:
        target = requirements.setdefault(names_by_code[fix["disciplina"]], {"pre": [], "co": []})
        for code in fix.get("adicionar_pre", []):
            if names_by_code[code] not in target["pre"]:
                target["pre"].append(names_by_code[code])
    return requirements


def load_meetings(period: str, aliases: dict[str, str]) -> list[Meeting]:
    """Horários do Eureca. Quando ele não traz dia e hora (laboratórios), usa o horário transcrito do site do curso."""
    meetings = []
    transcribed = defaultdict(list)
    transcribed_path = DATA_DIR / "raw" / f"horario-{period}.csv"
    if transcribed_path.exists():
        for row in read_csv(transcribed_path):
            code = aliases.get(compact(row["sigla"]))
            if code:
                start, end = row["hora"].split("-")
                transcribed[(int(code), row["turma"])].append((row["dia"], int(start), int(end)))

    for row in read_json(EURECA_DIR / f"horarios-{period}.json"):
        code, turma, room = row["codigo_da_disciplina"], row["turma"], row["codigo_da_sala"]
        if row["dia"] is not None and row["hora_de_inicio"]:
            start, end = int(row["hora_de_inicio"].split(":")[0]), int(row["hora_de_termino"].split(":")[0])
            meetings.append(Meeting(code, turma, WEEKDAYS.get(row["dia"]), start, end, room))
        elif (code, turma) in transcribed:
            meetings += [Meeting(code, turma, day, start, end, room) for day, start, end in transcribed[(code, turma)]]
        else:
            meetings.append(Meeting(code, turma, None, None, None, room))
    unique = {(m.code, m.turma, m.day, m.start, m.room): m for m in meetings}
    return list(unique.values())


def build_catalog(period: str | None = None) -> Catalog:
    period = period or latest_period()
    rows = defaultdict(dict)
    for curriculum in CURRICULA:
        for row in read_json(EURECA_DIR / f"curriculo-{curriculum}.json"):
            rows[row["codigo_da_disciplina"]][curriculum] = row

    ppc = {record["nome"]: record for record in read_json(PPC_PATH)}
    ppc_codes = {row["nome_ppc"]: int(row["codigo"]) for row in read_csv(CATALOG_DIR / "ppc_codigos.csv")}
    ppc_name_of = {code: name for name, code in ppc_codes.items()}

    by_name = defaultdict(list)
    for code, versions in rows.items():
        by_name[normalize(next(iter(versions.values()))["nome"])].append(code)

    disciplines = {}
    for codes in by_name.values():
        # Mesmo nome com dois códigos: o catálogo tem uma disciplina só, preferindo a do PPC e do currículo mais novo.
        main = sorted(codes, key=lambda c: (c not in ppc_name_of, "2023" not in rows[c], c))[0]
        versions = {year: row for code in codes for year, row in rows[code].items()}
        row = versions.get("2023") or versions["2017"]
        ppc_name = next((ppc_name_of[c] for c in codes if c in ppc_name_of), None)
        disciplines[main] = Discipline(
            code=main,
            name=ppc_name or title_case(row["nome"]),
            codes=tuple(sorted(codes)),
            curricula=tuple(sorted(versions)),
            kind=row["tipo"],
            semester=row["semestre_ideal"],
            hours=row["horas_totais"],
            credits=row["quantidade_de_creditos"],
            summary=ppc[ppc_name]["ementa"] if ppc_name else None,
        )
    code_to_main = {code: d.code for d in disciplines.values() for code in d.codes}
    names_by_code = {code: disciplines[main].name for code, main in code_to_main.items()}

    alias_rows = read_csv(CATALOG_DIR / "apelidos_disciplinas.csv")
    discipline_aliases = {compact(r["apelido"]): str(code_to_main[int(r["codigo"])]) for r in alias_rows}

    people = {row["id"]: row for row in read_csv(CATALOG_DIR / "pessoas.csv")}
    people_aliases = {compact(alias): pid for pid, row in people.items() for alias in row["apelidos"].split("|") if alias}
    by_teacher_id = {row["id_docente"]: pid for pid, row in people.items() if row["id_docente"]}

    teachers = defaultdict(list)
    for row in read_json(EURECA_DIR / f"turmas-{period}.json"):
        key = (code_to_main[row["codigo_da_disciplina"]], row["turma"])
        pid = by_teacher_id.get(str(row["id_docente"]))
        if pid and pid not in teachers[key]:
            teachers[key].append(pid)

    meetings = [
        Meeting(code_to_main[m.code], m.turma, m.day, m.start, m.end, m.room)
        for m in load_meetings(period, {key: value for key, value in discipline_aliases.items()})
    ]
    return Catalog(
        period=period,
        disciplines=disciplines,
        discipline_names={str(code): d.name for code, d in disciplines.items()},
        discipline_aliases=discipline_aliases,
        people=people,
        people_names={pid: row["nome"] for pid, row in people.items()},
        people_aliases=people_aliases,
        requirements=load_requirements(ppc, ppc_codes, names_by_code),
        teachers=dict(teachers),
        meetings=meetings,
    )


@lru_cache
def get_catalog() -> Catalog:
    return build_catalog()


def resolve_discipline(mention: str, catalog: Catalog | None = None) -> Resolution:
    catalog = catalog or get_catalog()
    return resolve(mention, catalog.discipline_names, catalog.discipline_aliases)


def find_discipline_mentions(text: str, catalog: Catalog | None = None) -> list[str]:
    """Disciplinas citadas no texto, reconhecidas por nome ou apelido, sem depender do LLM.

    Apelidos de 2 letras sem número ficam de fora porque são palavras comuns: "as", "es", "so", "ia".
    """
    catalog = catalog or get_catalog()
    plain = normalize(text)
    tokens = plain.split()
    found = []
    used = set()
    # Do mais longo ao mais curto, para "lab de p1" não reconhecer também "p1".
    for size in range(MAX_ALIAS_WORDS, 0, -1):
        for start in range(len(tokens) - size + 1):
            positions = set(range(start, start + size))
            candidate = "".join(tokens[start : start + size])
            usable = len(candidate) >= MIN_ALIAS_LENGTH or any(char.isdigit() for char in candidate)
            if usable and candidate in catalog.discipline_aliases and not positions & used:
                used |= positions
                if candidate not in found:
                    found.append(candidate)
    padded = f" {plain} "
    found += [name for name in catalog.discipline_names.values() if f" {normalize(name)} " in padded]
    return found


def resolve_person(mention: str, catalog: Catalog | None = None) -> Resolution:
    catalog = catalog or get_catalog()
    return resolve(mention, catalog.people_names, catalog.people_aliases)
