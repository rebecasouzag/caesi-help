"""Ferramentas sobre o FAQ e os documentos oficiais."""
from app.base.db import get_client
from app.conhecimento.busca import answerable, search_docs, search_faq
from app.ferramentas.evidencia import Evidencia, Resultado

CHUNK_COLUMNS = "id, document_id, content, locator, documents(title, status)"


def faq_evidence(entry: dict) -> Evidencia:
    sources = entry.get("sources") or []
    first = sources[0] if sources else {"doc": "faq", "trecho": entry["id"]}
    text = f"Pergunta: {entry['question']}\nResposta: {entry['answer']}"
    return Evidencia(
        f"faq:{entry['id']}", text, first["doc"], first["doc"], first["trecho"],
        procedencia=entry.get("provenance", "oficial"), validade=entry.get("valid_until"),
    )


def buscar_faq(pergunta: str, k: int = 5) -> Resultado:
    """Respostas curadas parecidas com a pergunta. São candidatas: semelhança de assunto não garante que respondam."""
    entries = [entry for entry in search_faq(pergunta, k=k) if answerable(entry)]
    return Resultado("encontrado", [faq_evidence(e) for e in entries]) if entries else Resultado("nao_encontrado")


def buscar_normas(pergunta: str, k: int = 8) -> Resultado:
    """Trechos de regulamentos, resoluções e páginas do curso."""
    hits = search_docs(pergunta, k=k)
    evidences = [
        Evidencia(f"{h['document_id']}:{h['id']}", h["content"], h["document_id"], h["title"], h["locator"], status=h["status"])
        for h in hits
    ]
    return Resultado("encontrado", evidences) if evidences else Resultado("nao_encontrado")


def ler_artigo(documento: str, artigo: str) -> Resultado:
    """Texto de um artigo ou seção específica de um documento."""
    rows = (
        get_client().table("chunks").select(CHUNK_COLUMNS)
        .eq("document_id", documento).eq("locator", artigo).execute().data
    )
    evidences = [
        Evidencia(f"{r['document_id']}:{r['id']}", r["content"], r["document_id"], r["documents"]["title"], r["locator"], status=r["documents"]["status"])
        for r in rows
    ]
    return Resultado("encontrado", evidences) if evidences else Resultado("nao_encontrado")
