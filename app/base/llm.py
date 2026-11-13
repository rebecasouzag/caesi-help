"""Acesso ao LLM. Para trocar de provedor, basta alterar get_llm()."""
import logging
import os
import time
from collections import deque
from functools import lru_cache

from groq import APIConnectionError, BadRequestError, InternalServerError, RateLimitError
from langchain_groq import ChatGroq
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
MAX_WAIT_SECONDS = 60.0  # acima disso (ex.: limite diário), é melhor falhar do que deixar o aluno esperando
TOOL_USE_FAILED = "tool_use_failed"

# Últimas chamadas, para medir tokens por pergunta.
usage_log: deque[dict] = deque(maxlen=1000)


@lru_cache
def get_llm() -> ChatGroq:
    # As novas tentativas ficam em ask(), que também trata saídas inválidas.
    return ChatGroq(model=os.environ["LLM_MODEL"], api_key=os.environ["LLM_API_KEY"], temperature=0, max_retries=0)


def retry_delay(error: Exception, attempt: int) -> float | None:
    """Segundos até a próxima tentativa, ou None se não vale tentar de novo."""
    if isinstance(error, RateLimitError):
        retry_after = error.response.headers.get("retry-after")
        delay = float(retry_after) if retry_after else BACKOFF_SECONDS * 2**attempt
        return delay if delay <= MAX_WAIT_SECONDS else None
    if isinstance(error, BadRequestError) and TOOL_USE_FAILED not in str(error):
        return None
    if isinstance(error, (BadRequestError, InternalServerError, APIConnectionError)):
        return BACKOFF_SECONDS * 2**attempt
    return None


def record_usage(schema: type[BaseModel], message) -> None:
    usage = getattr(message, "usage_metadata", None) or {}
    entry = {
        "schema": schema.__name__,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    usage_log.append(entry)
    logger.info("llm %s", entry)


def ask(schema: type[BaseModel], system: str, user: str) -> BaseModel:
    """Chamada com saída estruturada: toda resposta do LLM é validada pelo schema Pydantic."""
    chain = get_llm().with_structured_output(schema, include_raw=True)
    for attempt in range(MAX_ATTEMPTS):
        last_attempt = attempt == MAX_ATTEMPTS - 1
        try:
            result = chain.invoke([("system", system), ("human", user)])
        except (BadRequestError, RateLimitError, InternalServerError, APIConnectionError) as error:
            delay = retry_delay(error, attempt)
            if delay is None or last_attempt:
                raise
            logger.warning("llm %s: nova tentativa em %.1fs (%s)", schema.__name__, delay, type(error).__name__)
            time.sleep(delay)
            continue
        record_usage(schema, result["raw"])
        if result["parsed"] is not None:
            return result["parsed"]
        if last_attempt:
            raise ValueError(f"Saída inválida do LLM para {schema.__name__}: {result['parsing_error']}")
        logger.warning("llm %s: saída inválida, nova tentativa", schema.__name__)
