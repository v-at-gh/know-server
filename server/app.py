import logging
import time
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from server.db import get_server_db, init_server_db
from quizkb.observability import configure_logging, configure_telemetry
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger("server")
configure_logging("server")
configure_telemetry("server")

MAX_FEEDBACK_BATCH = 100
MAX_TEXT_FIELD = 8192  # generous; longer than any real feedback reason

@asynccontextmanager
async def lifespan(app: FastAPI):
    from opentelemetry import trace
    provider_type = type(trace.get_tracer_provider()).__name__
    logger.info(f"telemetry_provider_check: {provider_type}")
    init_server_db()
    yield

app = FastAPI(title="Know Public API", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

@app.get("/health")
async def health():
    return {"status": "ok"}

# Pydantic model matching the Android Feedback entity
class FeedbackPayload(BaseModel):
    client_uuid: str = Field(..., max_length=200)
    question_uuid: Optional[str] = Field(None, max_length=200)
    question_text: str = Field(..., max_length=MAX_TEXT_FIELD)
    selected_answer: str = Field(..., max_length=MAX_TEXT_FIELD)
    correct_answer: str = Field(..., max_length=MAX_TEXT_FIELD)
    language: str = Field(..., max_length=200)
    domain_slug: str = Field(..., max_length=200)
    article_slug: Optional[str] = Field(None, max_length=200)
    reason_code: str = Field(..., max_length=200)
    comment: str = Field(..., max_length=MAX_TEXT_FIELD)
    app_version: str = Field(..., max_length=200)
    locale: str = Field(..., max_length=200)
    client_created_at: int

@app.post("/api/feedback")
async def receive_feedback(payload: List[FeedbackPayload]):
    """Receives a batch of feedback from the Android app."""
    if len(payload) > MAX_FEEDBACK_BATCH:
        raise HTTPException(413, f"batch exceeds {MAX_FEEDBACK_BATCH} items")
    now = int(time.time() * 1000)
    inserted = 0
    
    with get_server_db() as conn:
        for item in payload:
            try:
                conn.execute("""
                    INSERT INTO feedback (
                        client_uuid, question_uuid, question_text, selected_answer, 
                        correct_answer, language, domain_slug, article_slug, 
                        reason_code, comment, app_version, locale, 
                        client_created_at, server_received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.client_uuid, item.question_uuid, item.question_text, item.selected_answer,
                    item.correct_answer, item.language, item.domain_slug, item.article_slug,
                    item.reason_code, item.comment, item.app_version, item.locale,
                    item.client_created_at, now
                ))
                inserted += 1
            except sqlite3.IntegrityError as e:
                # Duplicate client_uuid, skip
                logger.warning(f"IntegrityError: {e}")
                pass
        conn.commit()
        
    logger.info(f"Received batch of {len(payload)} feedback items. Inserted {inserted} new.")
    return {"status": "ok", "inserted": inserted}
