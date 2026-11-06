import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from ollama import Client as _OllamaClient

from .config import OLLAMA_HOST, MODEL, CONTEXT_LEN, OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS, EMBED_BATCH_SIZE
import logging

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbedRecord:
    embeddings: list[list[float]]
    prompt_tokens: int
    latency_ms: int
    created_at: str
    error: str | None = None


@dataclass(frozen=True)
class RequestRecord:
    model: str
    ctx: int
    predict: int
    prompt: str
    response: str
    prompt_tokens: int
    response_tokens: int
    latency_ms: int
    created_at: str
    error: str | None = None


def _get(obj: Any, key: str, default=None):
    """Достаёт поле из dict или pydantic-объекта (новый ollama это pydantic)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OllamaClient:
    def __init__(
        self,
        host: str = OLLAMA_HOST,
        model: str = MODEL,
        ctx: int = CONTEXT_LEN,
        embed_model: str = OLLAMA_EMBED_MODEL,
    ):
        self._client = _OllamaClient(host=host, timeout=300.0)
        self.default_model = model
        self.default_ctx = ctx
        self.embed_model = embed_model

    def query(
        self,
        prompt: str,
        *,
        model: str | None = None,
        ctx: int | None = None,
        predict: int = 128,
        think: bool = True,
        keep_alive: str = "30m",
    ) -> RequestRecord:
        m = model or self.default_model
        c = ctx or self.default_ctx
        start = time.time()
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", m)
            span.set_attribute("llm.predict", predict)
            span.set_attribute("llm.think", think)
            span.set_attribute("llm.ctx", c)
            try:
                resp = self._client.generate(
                    model=m,
                    prompt=prompt,
                    think=think,
                    keep_alive=keep_alive,
                    stream=False,
                    options={"num_ctx": c, "num_predict": predict},
                )
                latency = int((time.time() - start) * 1000)
                rec = RequestRecord(
                    model=m, ctx=c, predict=predict, prompt=prompt,
                    response=_get(resp, "response", "") or "",
                    prompt_tokens=_get(resp, "prompt_eval_count", 0) or 0,
                    response_tokens=_get(resp, "eval_count", 0) or 0,
                    latency_ms=latency,
                    created_at=datetime.now().isoformat(),
                    error=None,
                )
                span.set_attribute("llm.tokens_in", rec.prompt_tokens)
                span.set_attribute("llm.tokens_out", rec.response_tokens)
                span.set_attribute("llm.latency_ms", rec.latency_ms)
                return rec
            except Exception as e:
                latency = int((time.time() - start) * 1000)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return RequestRecord(
                    model=m, ctx=c, predict=predict, prompt=prompt,
                    response="", prompt_tokens=0, response_tokens=0,
                    latency_ms=latency,
                    created_at=datetime.now().isoformat(),
                    error=f"{type(e).__name__}: {e}",
                )

    def embed_batch(
        self,
        inputs: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        batch_size: int | None = None,
    ) -> EmbedRecord:
        m = model or self.embed_model
        d = dimensions or EMBED_DIMENSIONS
        b = batch_size or EMBED_BATCH_SIZE
        start = time.time()
        tracer = trace.get_tracer(__name__)

        if not inputs:
            return EmbedRecord(
                embeddings=[], prompt_tokens=0, latency_ms=0,
                created_at=datetime.now().isoformat(), error=None
            )

        with tracer.start_as_current_span("llm.embed") as span:
            span.set_attribute("llm.model", m)
            span.set_attribute("llm.embed_count", len(inputs))
            span.set_attribute("llm.embed_dimensions", d)
            try:
                all_embeddings = []
                total_tokens = 0

                for i in range(0, len(inputs), b):
                    chunk = inputs[i:i+b]
                    try:
                        resp = self._client.embed(model=m, input=chunk, dimensions=d)
                        vecs = _get(resp, "embeddings", None)
                        if vecs is not None:
                            all_embeddings.extend(vecs)
                        prompt_eval = _get(resp, "prompt_eval_count", 0) or 0
                        total_tokens += prompt_eval
                    except AttributeError:
                        if not hasattr(self, "_logged_embed_fallback"):
                            log.warning("Ollama client does not support .embed; falling back to .embeddings() loop")
                            self._logged_embed_fallback = True
                        for text in chunk:
                            resp = self._client.embeddings(model=m, prompt=text)
                            vec = _get(resp, "embedding", None)
                            if vec is not None:
                                all_embeddings.append(vec)
                            prompt_eval = _get(resp, "prompt_eval_count", 0) or 0
                            total_tokens += prompt_eval

                latency = int((time.time() - start) * 1000)
                span.set_attribute("llm.latency_ms", latency)
                span.set_attribute("llm.tokens_in", total_tokens)
                return EmbedRecord(
                    embeddings=all_embeddings,
                    prompt_tokens=total_tokens,
                    latency_ms=latency,
                    created_at=datetime.now().isoformat(),
                    error=None,
                )
            except Exception as e:
                latency = int((time.time() - start) * 1000)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return EmbedRecord(
                    embeddings=[],
                    prompt_tokens=0,
                    latency_ms=latency,
                    created_at=datetime.now().isoformat(),
                    error=f"{type(e).__name__}: {e}",
                )