"""Ingestão idempotente: manifest.yaml + data/raw + faq.yaml -> Supabase.

Uso: python -m app.ingestao.pipeline
"""
import os
import sys

from dotenv import load_dotenv

from app.base.config import DATA_DIR
from app.conhecimento.entidades import normalize
from app.conhecimento.manifest import Document, FaqEntry, load_all_faq, load_manifest, validate_sources
from app.ingestao.extratores import extract_chunks

INSERT_BATCH = 200

def format_passage(text: str) -> str:
    return f"passage: {text}"

def embed(model, texts: list[str], dim: int) -> list[list[float]]:
    if not texts:
        return []
    vectors = model.encode([format_passage(t) for t in texts], normalize_embeddings=True, batch_size=32)
    if vectors.shape[1] != dim:
        raise ValueError(f"O modelo gera vetores de {vectors.shape[1]} dimensões, mas EMBEDDING_DIM={dim}.")
    return vectors.tolist()

def insert_batched(client, table: str, rows: list[dict]) -> None:
    for start in range(0, len(rows), INSERT_BATCH):
        client.table(table).insert(rows[start : start + INSERT_BATCH]).execute()

def sync_documents(client, documents: list[Document], chunk_rows: dict[str, list[dict]]) -> None:
    """Deixa documents e chunks iguais ao manifest. Documentos com ingerir: false saem do banco (cascade nos trechos)."""
    wanted = {d.arquivo for d in documents if d.ingerir}
    existing = {row["id"] for row in client.table("documents").select("id").execute().data}
    stale = sorted(existing - wanted)
    if stale:
        client.table("documents").delete().in_("id", stale).execute()
    for document in documents:
        if not document.ingerir:
            continue
        client.table("documents").upsert({
            "id": document.arquivo,
            "title": document.titulo,
            "status": document.status,
            "topics": document.temas,
            "updated_at": document.atualizado_em.isoformat() if document.atualizado_em else None,
        }).execute()
        client.table("chunks").delete().eq("document_id", document.arquivo).execute()
        insert_batched(client, "chunks", chunk_rows[document.arquivo])

def sync_faq(client, entries: list[FaqEntry], embeddings: dict[str, list[float]],
             variation_embeddings: dict[str, list[list[float]]]) -> None:
    """Deixa o FAQ igual ao YAML, sem tocar nas entradas promovidas de escalonamentos (origin='escalonamento')."""
    existing = {row["id"]: row["origin"] for row in client.table("faq").select("id, origin").execute().data}
    wanted = {e.id for e in entries}
    conflicts = sorted(i for i in wanted if existing.get(i) == "escalonamento")
    if conflicts:
        raise ValueError(f"IDs do YAML já usados por entradas promovidas: {conflicts}")
    stale = sorted(i for i, origin in existing.items() if origin == "yaml" and i not in wanted)
    if stale:
        client.table("faq").delete().in_("id", stale).execute()
    for entry in entries:
        client.table("faq").upsert({
            "id": entry.id,
            "category": entry.categoria,
            "question": entry.pergunta,
            "answer": entry.resposta.strip(),
            "sources": [s.model_dump() for s in entry.fontes],
            "curriculum": entry.curriculo,
            "status": entry.status,
            "answer_by_curriculum": {year: text.strip() for year, text in entry.respostas_por_curriculo.items()},
            "origin": "yaml",
            "provenance": entry.origem,
            "valid_until": entry.valido_ate.isoformat() if entry.valido_ate else None,
            "embedding": embeddings[entry.id],
        }).execute()
        client.table("faq_variations").delete().eq("faq_id", entry.id).execute()
        rows = [
            {"faq_id": entry.id, "text": text, "embedding": vector}
            for text, vector in zip(entry.variacoes, variation_embeddings[entry.id])
        ]
        if rows:
            client.table("faq_variations").insert(rows).execute()

def count(client, table: str) -> int:
    return client.table(table).select("id", count="exact").limit(1).execute().count

def main() -> None:
    load_dotenv()
    from sentence_transformers import SentenceTransformer
    from supabase import create_client

    dim = int(os.environ["EMBEDDING_DIM"])
    documents = load_manifest(DATA_DIR / "manifest.yaml")
    faq = load_all_faq(DATA_DIR)
    validate_sources(documents, faq, DATA_DIR)

    # Tudo é preparado antes da primeira escrita: um erro aqui não deixa o banco pela metade.
    model = SentenceTransformer(os.environ["EMBEDDING_MODEL"])
    chunk_rows = {}
    for document in documents:
        if not document.ingerir:
            continue
        chunks = extract_chunks(document, DATA_DIR / "raw")
        vectors = embed(model, [f"{document.titulo}\n{c.locator}\n{c.content}" for c in chunks], dim)
        chunk_rows[document.arquivo] = [
            {
                "document_id": document.arquivo, "content": c.content, "locator": c.locator, "embedding": v,
                "search_text": normalize(f"{document.titulo} {c.locator} {c.content}"),
            }
            for c, v in zip(chunks, vectors)
        ]
        print(f"{document.arquivo}: {len(chunks)} trechos")
    faq_vectors = dict(zip((e.id for e in faq), embed(model, [e.pergunta for e in faq], dim)))
    variation_vectors = {e.id: embed(model, e.variacoes, dim) for e in faq}

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sync_documents(client, documents, chunk_rows)
    sync_faq(client, faq, faq_vectors, variation_vectors)

    for table in ("documents", "chunks", "faq", "faq_variations"):
        print(f"{table}: {count(client, table)}")


if __name__ == "__main__":
    sys.exit(main())
