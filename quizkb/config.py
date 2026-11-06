import os
import sqlite3
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STATE_DIR = REPO_ROOT / "state"
APP_DB_PATH = REPO_ROOT / "state" / "app.db"

# ---------- Logging ----------
# Logs root resolution now lives in quizkb/settings.py (portable across
# machines, env-overridable via KNOW_LOGS_ROOT). Names preserved here for
# backward compat with existing imports.
from quizkb.settings import get_settings as _get_settings

_settings = _get_settings()
LOGS_ROOT = _settings.logs_root
ACKNOWLEDGED_PATH = _settings.acknowledged_path
LOGGING_KEEP_LAST_RUNS = 100

# ---------- QAP packs ----------
QAPS_DIR = STATE_DIR / "qaps"
QAPS_INDEX = STATE_DIR / "manifest.json"
PACKS_JSON = STATE_DIR / "packs.json"

# ---------- Ollama ----------
OLLAMA_HOST = _settings.ollama_host
MODEL       = "gemma4"
CONTEXT_LEN = 65536
OLLAMA_EMBED_MODEL = "embeddinggemma:latest"
EMBED_DIMENSIONS = 128
EMBED_BATCH_SIZE = 64

# ---------- Predict budgets ----------
SYLLABUS_PREDICT     = 2048
ARTICLE_PREDICT      = 4096
QUESTIONS_PREDICT    = 4096
TRANSLATION_PREDICT  = 2048
DOMAIN_META_PREDICT  = 512
SINGLE_Q_PREDICT     = 1024
CLASSIFY_PREDICT     = 2048

# ---------- Paths ----------
OUTPUT_ROOT = _settings.output_root

# ---------- Languages ----------
SOURCE_LANG  = "en"
TARGET_LANGS = [
    "ru", "uk", "es", "de", "fr", "sr", "cs", "az", "hy", "ka",
    "zh", "hi", "ar", "bn", "pt", "id", "ur", "vi", "tr", "it",
    "kk", "tg", "uz"
]

LANG_NAMES = {
    "ru": "Russian", "uk": "Ukrainian", "es": "Spanish",
    "de": "German",  "fr": "French",    "sr": "Serbian",
    "cs": "Czech",
    "az": "Azerbaijani", "hy": "Armenian", "ka": "Georgian",
    "zh": "Simplified Chinese",
    "hi": "Hindi",
    "ar": "Arabic",
    "bn": "Bengali",
    "pt": "Portuguese",
    "id": "Indonesian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "tr": "Turkish",
    "it": "Italian",
    "kk": "Kazakh",
    "tg": "Tajik",
    "uz": "Uzbek",
}

# ---------- Content shape ----------
ARTICLES_PER_DOMAIN      = 10
QUESTIONS_PER_ARTICLE    = 5
DISTRACTORS_PER_QUESTION = 4
HINTS_PER_QUESTION       = 3

# ---------- Domains ----------
_DEFAULT_DOMAINS = [
    {"slug": "networking", "name": "Networking", "kind": "tool",
     "description": "TCP/IP, DNS, HTTP, TLS, routing, and the layered model of the internet"},
    {"slug": "linux", "name": "Linux", "kind": "tool",
     "description": "Shell, processes, filesystem, permissions, systemd, and system administration"},
    {"slug": "docker", "name": "Docker", "kind": "tool",
     "description": "Containers, images, Dockerfiles, networking, volumes, and Compose"},
    {"slug": "kubernetes", "name": "Kubernetes", "kind": "tool",
     "description": "Pods, deployments, services, ingress, and cluster operations"},
    {"slug": "python", "name": "Python", "kind": "programming-languages",
     "description": "Syntax, data structures, OOP, standard library, and common idioms"},
    {"slug": "git", "name": "Git", "kind": "tool",
     "description": "Version control fundamentals: commits, branches, merges, rebases, remotes, and collaboration workflows"},
    {"slug": "cicd", "name": "CI/CD", "kind": "practice",
     "description": "Build pipelines, automated testing, artifact management, deployment strategies, GitOps"},
    {"slug": "iac", "name": "Infrastructure as Code", "kind": "practice",
     "description": "Terraform, Ansible, Pulumi, declarative configuration, state management, drift detection"},
    {"slug": "observability", "name": "Observability", "kind": "practice",
     "description": "Structured logging, metrics, distributed tracing, alerting, SLO/SLI basics"},
    {"slug": "analyst-requirements", "name": "Requirements Engineering", "kind": "method",
     "description": "Elicitation, user stories, acceptance criteria, traceability"},
    {"slug": "analyst-modeling", "name": "Modeling", "kind": "method",
     "description": "UML, BPMN, data flow diagrams, sequence and state diagrams"},
    {"slug": "analyst-stakeholders", "name": "Stakeholder Management", "kind": "method",
     "description": "Interviews, negotiation, prioritization, conflict resolution"},
    {"slug": "mathematics", "name": "Mathematics", "kind": "science",
     "description": "Arithmetic, algebra, geometry, probability, and mathematical reasoning"},
    {"slug": "physics", "name": "Physics", "kind": "science",
     "description": "Mechanics, thermodynamics, waves, optics, and modern physics fundamentals"},
    {"slug": "biology", "name": "Biology", "kind": "science",
     "description": "Cells, genetics, evolution, ecology, and the tree of life"},
    {"slug": "geography", "name": "Geography", "kind": "science",
     "description": "Continents, climate, biomes, plate tectonics, and human geography"},
    {"slug": "logic", "name": "Logic", "kind": "reasoning",
     "description": "Deductive and inductive reasoning, common fallacies, and probabilistic thinking"},
]

_DEFAULT_CAMPAIGNS = [
    {
        "slug": "devops", "name": "DevOps Engineer",
        "description": "From pipelines to production: build, ship, and observe systems.",
        "order": 10,
        "domainSlugs": ["git", "cicd", "iac", "observability"],
        "prerequisiteDomainSlugs": ["linux", "docker", "kubernetes"],
    },
    {
        "slug": "systems-analyst", "name": "Systems Analyst",
        "description": "Turn business needs into specifications: elicit, model, negotiate.",
        "order": 20,
        "domainSlugs": ["analyst-requirements", "analyst-modeling", "analyst-stakeholders"],
        "prerequisiteDomainSlugs": [],
    },
    {
        "slug": "basics",
        "name": "Basics",
        "description": "The foundations everyone should know: math, science, geography, and reasoning.",
        "order": 1,
        "domainSlugs": ["mathematics", "physics", "biology", "geography", "logic"],
        "prerequisiteDomainSlugs": [],
    }
]

def _load_config_from_db():
    """Attempts to load domain and campaign configurations from the SQLite CMS database (`app.db`).
    
    If `app.db` exists and contains records, returns the loaded domains and campaigns.
    Otherwise, returns `(None, None)` to trigger fallback to hardcoded default configurations
    (`_DEFAULT_DOMAINS` and `_DEFAULT_CAMPAIGNS`).
    """
    if not APP_DB_PATH.exists():
        return None, None
    try:
        conn = sqlite3.connect(APP_DB_PATH)
        conn.row_factory = sqlite3.Row
        
        d_rows = conn.execute("SELECT * FROM domains").fetchall()
        if not d_rows:
            return None, None # Пустая БД
            
        domains = [dict(r) for r in d_rows]
        
        c_rows = conn.execute("SELECT * FROM campaigns").fetchall()
        campaigns = []
        for r in c_rows:
            c = dict(r)
            c["prerequisiteDomainSlugs"] = json.loads(c.pop("prerequisite_domain_slugs"))
            c["domainSlugs"] = [dr["domain_slug"] for dr in conn.execute(
                "SELECT domain_slug FROM campaign_domains WHERE campaign_slug = ? ORDER BY position", (c["slug"],)
            )]
            campaigns.append(c)
            
        conn.close()
        return domains, campaigns
    except Exception as e:
        print(f"Warning: Failed to load config from DB: {e}")
        return None, None

_db_domains, _db_campaigns = _load_config_from_db()

# Экспортируем переменные, которые ожидает остальной код
DOMAINS = _db_domains if _db_domains else _DEFAULT_DOMAINS
CAMPAIGNS = _db_campaigns if _db_campaigns else _DEFAULT_CAMPAIGNS

# ---------- Task scheduling ----------
TASK_PRIORITIES = {
    # Generation (baseline)
    "bootstrap": 0,
    "generate": 0,
    "translate": 0,
    "ensure_meta": 0,
    "recover": 0,
    "translate_single_q": 0,
    "generate_article_questions": 0,
    # Reindex (user-initiated > auto > maintenance)
    "reindex_domain": 20,
    "reindex_missing": 10,
    "reindex_prune": 5,
    "reindex_full": -10,
}

REINDEX_ACTIONS = frozenset({
    "reindex_full", "reindex_missing", "reindex_prune", "reindex_domain",
})

AUTO_REINDEX_AFTER_GENERATE = os.environ.get("KNOW_AUTO_REINDEX", "0").lower() in ("1", "true", "yes")

