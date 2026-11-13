"""Contatos das pessoas e setores do catálogo. Pessoa sem e-mail na base não é devolvida."""
from app.conhecimento.catalogo import get_catalog, resolve_person


def search_contacts(name: str) -> list[dict]:
    catalog = get_catalog()
    people = (catalog.people[key] for key in resolve_person(name, catalog).keys)
    return [person for person in people if person["email_principal"]]
