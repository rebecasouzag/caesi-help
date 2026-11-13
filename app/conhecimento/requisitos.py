"""Pré-requisitos e correquisitos: reconstrução a partir do PPC e relação entre disciplinas."""
import re


def rebuild_requirements(raw: list[str], names: list[str]) -> tuple[list[str], list[str]]:
    """Reconstrói os requisitos que a extração do PPC quebrou.

    Nomes com " e " vieram separados ("Estruturas de Dados", "Algoritmos"), o rótulo "CRÉDITOS" colou em um nome e
    os correquisitos ficaram no meio do texto. Um trecho sem correspondência é mantido como está.
    """
    def plain(text: str) -> str:
        return " ".join(re.sub(r"\ba\b", " ", text.lower()).split())

    by_plain = {plain(name): name for name in names}
    pieces = []
    for item in raw:
        before, mark, after = item.partition("CO-REQUISITO:")
        if before.strip():
            pieces.append((re.sub(r"^créditos\s+", "", before.strip()), "pre"))
        if mark:
            pieces.append((after.strip(), "co"))

    requirements = {"pre": [], "co": []}
    position = 0
    while position < len(pieces):
        text, kind = pieces[position]
        following = pieces[position + 1][0] if position + 1 < len(pieces) else ""
        joined = by_plain.get(plain(f"{text} e {following}"))
        prefixed = [name for name in names if plain(name).startswith(plain(f"{text} e "))]
        if joined:
            position += 1
        name = joined or by_plain.get(plain(text)) or (prefixed[0] if len(prefixed) == 1 else text)
        if name not in requirements[kind]:
            requirements[kind].append(name)
        position += 1
    return requirements["pre"], requirements["co"]


def prerequisite_chain(start: str, end: str, requirements: dict) -> list[str] | None:
    """Cadeia start → ... → end, em que cada disciplina é pré-requisito da seguinte."""
    pending = [[end]]
    seen = {end}
    while pending:
        path = pending.pop(0)
        for prerequisite in requirements.get(path[0], {}).get("pre", []):
            if prerequisite == start:
                return [start, *path]
            if prerequisite not in seen:
                seen.add(prerequisite)
                pending.append([prerequisite, *path])
    return None


def describe_relation(first: str, second: str, requirements: dict) -> str:
    """Relação entre duas disciplinas ao cursá-las no mesmo período."""
    if second in requirements.get(first, {}).get("co", []) or first in requirements.get(second, {}).get("co", []):
        return f"{first} e {second} são correquisitos: devem ser cursadas no mesmo período."
    for before, after in ((first, second), (second, first)):
        chain = prerequisite_chain(before, after, requirements)
        if chain:
            kind = "direto" if len(chain) == 2 else "indireto"
            return (
                f"{before} é pré-requisito {kind} de {after} (cadeia: {' → '.join(chain)}). "
                f"Em regra, não podem ser cursadas no mesmo período: {after} só pode ser cursada depois."
            )
    return (
        f"Nenhuma entre {first} e {second} é pré-requisito da outra, direta ou indiretamente, e não são correquisitos. "
        "Podem ser cursadas no mesmo período, desde que o aluno já tenha cumprido os pré-requisitos de cada uma."
    )
