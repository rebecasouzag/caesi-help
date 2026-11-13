"""Copia para data/eureca a grade dos currículos do curso e as turmas e horários do período atual.

Uso: python -m app.ingestao.sync_eureca

A grade muda por período e a API pode ser lenta, então o bot lê estes arquivos em vez de consultar a API a cada
pergunta. Por serem versionados, o git mostra o que mudou. O calendário não entra aqui: é consultado ao vivo.
"""
import json
import pathlib
import sys

import httpx

from app.base import eureca
from app.base.config import DATA_DIR

EURECA_DIR = DATA_DIR / "eureca"
CURRICULA = ("2017", "2023")
CURRICULUM_FIELDS = ("codigo_da_disciplina", "nome", "tipo", "semestre_ideal", "horas_totais", "quantidade_de_creditos", "status")
CLASS_FIELDS = ("codigo_da_disciplina", "turma", "periodo", "carga_horaria", "tipo", "id_docente", "nome_docente")
SCHEDULE_FIELDS = ("codigo_da_disciplina", "nome_da_disciplina", "turma", "periodo", "dia", "hora_de_inicio", "hora_de_termino", "codigo_da_sala")
CALENDAR_FIELDS = ("id", "periodo", "campus", "inicio_das_matriculas", "inicio_das_aulas", "um_quarto_do_periodo", "um_terco_do_periodo", "ultimo_dia_para_registro_de_notas", "numero_de_semanas")


def select(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    """Guarda só os campos usados, em ordem estável, para o diff do git mostrar apenas mudanças reais."""
    kept = [{field: row.get(field) for field in fields} for row in rows]
    return sorted(kept, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))


def only_course_disciplines(rows: list[dict], codes: set[int]) -> tuple[list[dict], set[int]]:
    """Mantém as linhas de disciplinas dos currículos do curso. Devolve também os códigos descartados.

    A comparação é pelo código porque há disciplinas de outros cursos com o mesmo nome (Inglês, Cálculo II...).
    """
    kept = [row for row in rows if row["codigo_da_disciplina"] in codes]
    return kept, {row["codigo_da_disciplina"] for row in rows} - codes


def snapshot_files(period: str, client: httpx.Client | None = None) -> tuple[dict[str, list[dict]], dict[int, str]]:
    """Devolve nome do arquivo -> conteúdo e as disciplinas descartadas por não serem do curso."""
    files, codes = {}, set()
    for curriculum in CURRICULA:
        rows = eureca.curriculum_disciplines(curriculum, client)
        files[f"curriculo-{curriculum}.json"] = select(rows, CURRICULUM_FIELDS)
        codes |= {row["codigo_da_disciplina"] for row in rows}

    classes, _ = only_course_disciplines(eureca.classes(period, client), codes)
    schedules = eureca.schedules(period, client)
    names = {row["codigo_da_disciplina"]: row["nome_da_disciplina"] for row in schedules}
    schedules, dropped = only_course_disciplines(schedules, codes)
    files[f"turmas-{period}.json"] = select(classes, CLASS_FIELDS)
    files[f"horarios-{period}.json"] = select(schedules, SCHEDULE_FIELDS)
    return files, {code: names[code] for code in dropped}


def write_files(files: dict[str, list[dict]], directory: pathlib.Path = EURECA_DIR) -> dict[str, list[str]]:
    """Grava o que mudou. Resultado vazio não apaga a cópia anterior: uma falha da API não pode esvaziar a base."""
    directory.mkdir(exist_ok=True)
    report = {"changed": [], "unchanged": [], "empty": []}
    for name, rows in files.items():
        path = directory / name
        if not rows:
            report["empty"].append(name)
            continue
        content = json.dumps(rows, ensure_ascii=False, indent=1) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == content:
            report["unchanged"].append(name)
            continue
        path.write_text(content, encoding="utf-8", newline="\n")
        report["changed"].append(name)
    return report


def main() -> int:
    with httpx.Client() as client:
        period = eureca.current_period(client)
        files, dropped = snapshot_files(period, client)
        # Cópia para o bot continuar informando as datas se a API estiver fora do ar.
        files["calendarios.json"] = select(eureca.calendars(client), CALENDAR_FIELDS)
    report = write_files(files)
    print(f"Período: {period}")
    for kind, names in report.items():
        print(f"{kind}: {names}")
    print(f"Fora dos currículos do curso, descartadas: {sorted(dropped.values())}")
    return 1 if report["empty"] else 0


if __name__ == "__main__":
    sys.exit(main())
