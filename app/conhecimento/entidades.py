"""Associa o que o aluno escreve a uma entidade da base (disciplina ou pessoa).

A busca vai do mais confiável ao menos confiável e para no primeiro nível que achar algo: nome exato, apelido,
mesmas palavras, parte do nome e, por último, aproximação por palavra (erro de digitação ou abreviação).
Ambiguidade é um resultado, não uma falha: quem chamou pode perguntar ao aluno qual delas ele quis dizer.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

IGNORED_WORDS = {
    "a", "o", "de", "da", "do", "das", "dos", "e", "em", "para", "com", "disciplina", "cadeira",
    "prof", "profa", "professor", "professora",
}
NUMERALS = {"1": "i", "2": "ii", "3": "iii", "4": "iv"}
MIN_FUZZY_LENGTH = 5
MIN_PREFIX_LENGTH = 4
MIN_SIMILARITY = 0.85


@dataclass(frozen=True)
class Resolution:
    status: Literal["resolvido", "ambiguo", "nao_encontrado"]
    keys: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"\w+", plain))


def words(text: str) -> set[str]:
    tokens = (NUMERALS.get(token, token) for token in normalize(text).split())
    return {token for token in tokens if token not in IGNORED_WORDS}


def similar_word(wanted: str, candidate: str) -> bool:
    if wanted == candidate:
        return True
    if len(wanted) >= MIN_PREFIX_LENGTH and candidate.startswith(wanted):
        return True
    return len(wanted) >= MIN_FUZZY_LENGTH and SequenceMatcher(None, wanted, candidate).ratio() >= MIN_SIMILARITY


def compact(text: str) -> str:
    return normalize(text).replace(" ", "")


def resolve(mention: str, names: dict[str, str], aliases: dict[str, str]) -> Resolution:
    """names: chave -> nome; aliases: apelido compactado -> chave."""
    wanted = words(mention)
    if not wanted:
        return Resolution("nao_encontrado")

    target = normalize(mention)
    exact = [key for key, name in names.items() if normalize(name) == target]
    if exact:
        return Resolution("resolvido", exact[:1])
    if compact(mention) in aliases:
        return Resolution("resolvido", [aliases[compact(mention)]])

    name_words = {key: words(name) for key, name in names.items()}
    levels = (
        lambda parts: wanted == parts,
        lambda parts: wanted <= parts,
        lambda parts: all(any(similar_word(w, part) for part in parts) for w in wanted),
    )
    for matches_level in levels:
        found = [key for key, parts in name_words.items() if matches_level(parts)]
        if len(found) == 1:
            return Resolution("resolvido", found)
        if found:
            return Resolution("ambiguo", found)
    return Resolution("nao_encontrado")
