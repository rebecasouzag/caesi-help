"""Confere se as fontes de dados se ligam entre si, antes que o erro apareça para o aluno.

Uso: python -m app.ingestao.validar_dados
"""
import sys
from collections import Counter

import yaml

from app.base.config import DATA_DIR
from app.conhecimento.catalogo import CATALOG_DIR, EURECA_DIR, build_catalog, latest_period, read_csv, read_json
from app.conhecimento.manifest import load_all_faq, load_manifest, validate_sources

KNOWN_WITHOUT_CODE = {"Tópicos em Ciência da Computação I", "Tópicos em Ciência da Computação II"}  # ainda sem par no Eureca


def validate() -> list[str]:
    errors = []
    period = latest_period()
    catalog = build_catalog(period)
    codes = {code for discipline in catalog.disciplines.values() for code in discipline.codes}

    for row in read_json(EURECA_DIR / f"turmas-{period}.json") + read_json(EURECA_DIR / f"horarios-{period}.json"):
        if row["codigo_da_disciplina"] not in codes:
            errors.append(f"Disciplina {row['codigo_da_disciplina']} no período {period} fora dos currículos.")

    teacher_ids = {row["id_docente"] for row in catalog.people.values()}
    for row in read_json(EURECA_DIR / f"turmas-{period}.json"):
        if str(row["id_docente"]) not in teacher_ids:
            errors.append(f"Docente sem pessoa em pessoas.csv: {row['nome_docente']} ({row['id_docente']}).")

    for row in read_csv(CATALOG_DIR / "apelidos_disciplinas.csv"):
        if int(row["codigo"]) not in codes:
            errors.append(f"Apelido '{row['apelido']}' aponta para o código inexistente {row['codigo']}.")

    ppc_names = {row["nome"] for row in read_json(DATA_DIR / "raw" / "disciplinas.json")}
    mapped = {row["nome_ppc"]: int(row["codigo"]) for row in read_csv(CATALOG_DIR / "ppc_codigos.csv")}
    for name in sorted(ppc_names - set(mapped) - KNOWN_WITHOUT_CODE):
        errors.append(f"Disciplina do PPC sem código: {name}.")
    for name, code in mapped.items():
        if code not in codes:
            errors.append(f"PPC '{name}' aponta para o código inexistente {code}.")

    fixes = yaml.safe_load((CATALOG_DIR / "correcoes_requisitos.yaml").read_text(encoding="utf-8")) or []
    for fix in fixes:
        for code in [fix["disciplina"], *fix.get("adicionar_pre", [])]:
            if code not in codes:
                errors.append(f"Correção de requisito com código inexistente: {code}.")

    known = set(ppc_names) | {d.name for d in catalog.disciplines.values()}
    for name, requirement in catalog.requirements.items():
        for required in requirement["pre"] + requirement["co"]:
            if required not in known:
                errors.append(f"Requisito de {name} não é uma disciplina conhecida: {required}.")

    repeated = [pid for pid, n in Counter(row["id"] for row in read_csv(CATALOG_DIR / "pessoas.csv")).items() if n > 1]
    errors += [f"Pessoa repetida: {pid}." for pid in repeated]

    faq = load_all_faq(DATA_DIR)
    ids = {entry.id for entry in faq}
    errors += [f"'{e.id}' complementa uma entrada inexistente: {e.complementa}." for e in faq if e.complementa and e.complementa not in ids]
    errors += [f"'{e.id}' é da comunidade mas está como revisado." for e in faq if e.origem == "comunidade" and e.status == "revisado"]
    try:
        validate_sources(load_manifest(DATA_DIR / "manifest.yaml"), faq, DATA_DIR)
    except FileNotFoundError as error:
        errors.append(str(error))
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"ERRO: {error}")
    print("Dados consistentes." if not errors else f"{len(errors)} erro(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
