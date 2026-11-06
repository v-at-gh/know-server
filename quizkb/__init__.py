from .config import (
    OLLAMA_HOST, MODEL, CONTEXT_LEN,
    SYLLABUS_PREDICT, ARTICLE_PREDICT, QUESTIONS_PREDICT,
    TRANSLATION_PREDICT, DOMAIN_META_PREDICT,
    SINGLE_Q_PREDICT, CLASSIFY_PREDICT,
    OUTPUT_ROOT, SOURCE_LANG, TARGET_LANGS, LANG_NAMES,
    ARTICLES_PER_DOMAIN, QUESTIONS_PER_ARTICLE,
    DISTRACTORS_PER_QUESTION, HINTS_PER_QUESTION,
    DOMAINS, CAMPAIGNS,
    LOGS_ROOT, LOGGING_KEEP_LAST_RUNS,
    QAPS_DIR, QAPS_INDEX,
    ACKNOWLEDGED_PATH
)
from .io_utils import load_json, save_json, save_json_atomic, ensure_dir, new_uuid, strip_fences
from .progress import Progress, default_progress
from .parsing import (
    parse_syllabus, parse_questions,
    DISTRACTOR_WHY_FORBIDDEN,
)
from . import campaigns
from . import domain_meta
from . import jobs
from . import packs
from . import recovery
from . import translation
from . import release
from . import issues
from . import users


def __getattr__(name: str):
    """Lazy access to the LLM-bound symbols.

    `client` (and transitively `ask`) pull in the `ollama` package. Consumers
    that only need config / io_utils / release — notably the packaging scripts
    in qap-pipeline — must not be forced to install `ollama`. PEP 562 defers
    these imports until first access.
    """
    if name == "OllamaClient":
        from .client import OllamaClient
        return OllamaClient
    if name == "RequestRecord":
        from .client import RequestRecord
        return RequestRecord
    if name == "translate_campaign":
        from .translation import translate_campaign
        return translate_campaign
    if name == "translate_campaign_into":
        from .translation import translate_campaign_into
        return translate_campaign_into
    raise AttributeError(f"module 'quizkb' has no attribute {name!r}")


__all__ = [
    "OLLAMA_HOST", "MODEL", "CONTEXT_LEN",
    "SYLLABUS_PREDICT", "ARTICLE_PREDICT", "QUESTIONS_PREDICT",
    "TRANSLATION_PREDICT", "DOMAIN_META_PREDICT",
    "SINGLE_Q_PREDICT", "CLASSIFY_PREDICT",
    "OUTPUT_ROOT", "SOURCE_LANG", "TARGET_LANGS", "LANG_NAMES",
    "ARTICLES_PER_DOMAIN", "QUESTIONS_PER_ARTICLE",
    "DISTRACTORS_PER_QUESTION", "HINTS_PER_QUESTION",
    "DOMAINS",
    "load_json", "save_json", "save_json_atomic",
    "ensure_dir", "new_uuid", "strip_fences",
    "Progress", "default_progress",
    "OllamaClient", "RequestRecord",
    "parse_syllabus", "parse_questions",
    "DISTRACTOR_WHY_FORBIDDEN",
    "LOGS_ROOT", "LOGGING_KEEP_LAST_RUNS",
    "QAPS_DIR", "QAPS_INDEX",
    "campaigns", "domain_meta", "jobs", "packs", "recovery",
    "translation", "release",
    "translate_campaign", "translate_campaign_into",
    "ACKNOWLEDGED_PATH", "issues", "users"
]