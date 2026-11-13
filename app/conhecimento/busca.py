"""Busca semântica no FAQ e nos trechos dos documentos. O match_chunks já exclui documentos revogados."""
import datetime as dt

from app.base.db import get_client
from app.base.embeddings import embed_query, rerank_scores
from app.conhecimento.entidades import normalize

RERANK_CANDIDATES = 30


def answerable(entry: dict) -> bool:
    """Rascunho ainda não foi conferido por ninguém do CA, e entrada vencida não vale mais: nenhum chega ao aluno."""
    expired = bool(entry.get("valid_until")) and entry["valid_until"] < dt.date.today().isoformat()
    return entry["status"] != "rascunho" and not expired


def search_faq(question: str, k: int = 5) -> list[dict]:
    params = {"query_embedding": embed_query(question), "k": k}
    return get_client().rpc("match_faq", params).execute().data

def search_docs(question: str, k: int = 8, mode: str = "vetorial") -> list[dict]:
    """vetorial: só por sentido. hibrida: sentido e palavras exatas, fundidos por posição (RRF).
    +rerank: traz mais candidatos e o cross-encoder escolhe os k melhores."""
    base_mode, _, rerank = mode.partition("+")
    limit = RERANK_CANDIDATES if rerank else k
    params = {"query_embedding": embed_query(question), "k": limit}
    if base_mode == "hibrida":
        hits = get_client().rpc("match_chunks_hybrid", {**params, "query_text": normalize(question)}).execute().data
    else:
        hits = get_client().rpc("match_chunks", params).execute().data
    if not rerank or not hits:
        return hits
    scores = rerank_scores(question, [f"{h['title']}\n{h['locator']}\n{h['content']}" for h in hits])
    ranked = sorted(zip(hits, scores), key=lambda pair: pair[1], reverse=True)
    return [{**hit, "rerank_score": score} for hit, score in ranked[:k]]
