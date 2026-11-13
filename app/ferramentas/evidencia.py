"""Formato comum das ferramentas. Uma Evidencia é um fato com origem; o LLM cita, e o código monta a fonte."""
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Evidencia:
    id: str
    texto: str
    fonte: str  # documento de origem
    titulo: str  # nome da fonte, como aparece na resposta
    trecho: str  # artigo, seção ou assunto dentro da fonte
    status: str = "vigente"
    procedencia: Literal["oficial", "ca", "comunidade"] = "oficial"
    url: str | None = None
    validade: str | None = None  # a partir de quando deixa de valer, se houver

    def as_chunk(self) -> dict:
        """Formato de trecho usado pelo grafo atual."""
        return {
            "id": self.id, "document_id": self.fonte, "title": self.titulo, "status": self.status,
            "content": self.texto, "locator": self.trecho, "similarity": 1.0,
        }


@dataclass(frozen=True)
class Resultado:
    """encontrado: há evidências. ambiguo: perguntar qual das opções. nao_encontrado: a base não tem.
    indisponivel: a fonte falhou, o que é diferente de a base não ter a informação."""
    status: Literal["encontrado", "ambiguo", "nao_encontrado", "indisponivel"]
    evidencias: list[Evidencia] = field(default_factory=list)
    opcoes: list[str] = field(default_factory=list)
    mensagem: str | None = None
