"""Modelos locais. Bi-encoder: embeddings das perguntas (o e5 exige o prefixo "query: "). Cross-encoder: reordenação."""
import os
from functools import lru_cache

from sentence_transformers import CrossEncoder, SentenceTransformer

DEFAULT_RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

@lru_cache
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(os.environ["EMBEDDING_MODEL"])

def embed_query(text: str) -> list[float]:
    return get_embedding_model().encode(f"query: {text}", normalize_embeddings=True).tolist()


@lru_cache
def get_rerank_model() -> CrossEncoder:
    return CrossEncoder(os.getenv("RERANK_MODEL", DEFAULT_RERANK_MODEL))


def rerank_scores(question: str, texts: list[str]) -> list[float]:
    """Nota de cada par (pergunta, texto), lidos juntos pelo modelo. Mais preciso e mais lento que comparar vetores."""
    return [float(score) for score in get_rerank_model().predict([(question, text) for text in texts])]
