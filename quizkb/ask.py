import logging
from .client import OllamaClient

log = logging.getLogger(__name__)


def make_ask(client: OllamaClient, logger):
    def ask(prompt, *, predict, label="", think=True):
        rec = client.query(prompt, predict=predict, think=think)
        logger.llm(
            label=label,
            prompt=prompt,
            response=rec.response or "",
            tokens_in=rec.prompt_tokens,
            tokens_out=rec.response_tokens,
            latency_ms=rec.latency_ms,
            model=client.default_model,
            think=think,
            error=rec.error,
        )
        if rec.error:
            log.error("[%s] %s", label, rec.error)
            return None
        log.info("[%s] %d tok out, %d ms", label, rec.response_tokens, rec.latency_ms)
        return rec.response

    return ask