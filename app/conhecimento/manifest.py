"""Metadados dos documentos e entradas do FAQ, como definidos em manifest.yaml e faq.yaml."""
import datetime as dt
import pathlib
from collections import Counter
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from app.base.config import DATA_DIR

class Document(BaseModel):
    arquivo: str
    titulo: str
    temas: list[str] = Field(default_factory=list)
    status: Literal["vigente", "revogada", "desatualizado"]
    atualizado_em: dt.date | None = None
    ingerir: bool
    url: str | None = None  # página do site baixada por app.sync_site
    secoes: list[str] = Field(default_factory=list)  # só estas seções da página entram na base
    excluir_secoes: list[str] = Field(default_factory=list)  # seções que outro documento já cobre
    aviso: str | None = None  # acrescentado às respostas que usam este documento

class FaqSource(BaseModel):
    doc: str
    trecho: str

class FaqEntry(BaseModel):
    id: str
    categoria: str
    pergunta: str
    variacoes: list[str] = Field(default_factory=list)
    resposta: str
    fontes: list[FaqSource] = Field(default_factory=list)
    curriculo: Literal["todos", "2017", "2023", "depende"]
    status: Literal["rascunho", "confirmar", "revisado"]
    respostas_por_curriculo: dict[Literal["2017", "2023"], str] = Field(default_factory=dict)
    origem: Literal["oficial", "ca", "comunidade"] = "oficial"
    valido_ate: dt.date | None = None  # depois disso, a entrada deixa de ser respondida
    complementa: str | None = None  # entrada relacionada
    nota_revisao: str | None = None  # o que falta conferir

    @model_validator(mode="after")
    def depende_tem_as_duas_respostas(self):
        if self.curriculo == "depende" and set(self.respostas_por_curriculo) != {"2017", "2023"}:
            raise ValueError(f"'{self.id}' depende do currículo e precisa de respostas para 2017 e 2023.")
        return self

@lru_cache
def load_document_fixes(kind: str) -> dict[str, list[dict]]:
    """Correções de texto por documento. kind: "pdf" ou "site"."""
    return yaml.safe_load((DATA_DIR / "catalogo" / "correcoes_documentos.yaml").read_text(encoding="utf-8"))[kind]


def load_manifest(path: pathlib.Path) -> list[Document]:
    return [Document(**item) for item in yaml.safe_load(path.read_text(encoding="utf-8"))]

def load_faq(path: pathlib.Path) -> list[FaqEntry]:
    entries = [FaqEntry(**item) for item in yaml.safe_load(path.read_text(encoding="utf-8"))]
    repeated = [faq_id for faq_id, n in Counter(e.id for e in entries).items() if n > 1]
    if repeated:
        raise ValueError(f"IDs repetidos no FAQ: {repeated}")
    return entries

def load_all_faq(data_dir: pathlib.Path) -> list[FaqEntry]:
    entries = load_faq(data_dir / "faq.yaml") + load_faq(data_dir / "faq-comunidade.yaml")
    repeated = [faq_id for faq_id, n in Counter(e.id for e in entries).items() if n > 1]
    if repeated:
        raise ValueError(f"IDs repetidos entre os arquivos de FAQ: {repeated}")
    return entries


def validate_sources(documents: list[Document], faq: list[FaqEntry], data_dir: pathlib.Path) -> None:
    raw = data_dir / "raw"
    # Páginas só monitoradas (url + ingerir: false) não têm arquivo local.
    missing = [d.arquivo for d in documents if (d.ingerir or not d.url) and not (raw / d.arquivo).exists()]
    if missing:
        raise FileNotFoundError(f"Arquivos do manifest ausentes em data/raw: {missing}")
    for entry in faq:
        for source in entry.fontes:
            if not (raw / source.doc).exists() and not (data_dir / source.doc).exists():
                raise FileNotFoundError(f"Fonte inexistente em '{entry.id}': {source.doc}")
