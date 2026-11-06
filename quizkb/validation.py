import re
from quizkb.io_utils import load_json
from quizkb.config import OUTPUT_ROOT

def structure_signature(text: str) -> tuple:
    """Rough structural fingerprint. All four counters are 'how many raw regex
    matches', not 'how many logical elements'. That's fine for comparison
    purposes: we only care that source and translation agree.
    """
    return (
        len(re.findall(r"^##\s", text, re.M)),
        len(re.findall(r"^```", text, re.M)),
        len(re.findall(r"\$\$", text)),           # 2 per display formula
        len(re.findall(r"(?<!\$)\$(?!\$)", text)),
    )

def count_questions_for_article(lang, domain, slug) -> int:
    """Сколько вопросов в языке lang привязано к статье domain/slug."""
    ds = load_json(OUTPUT_ROOT / "datasets" / lang / f"{domain}.json", []) or []
    key = f"{domain}/{slug}"
    return sum(1 for q in ds if q.get("relatedArticleSlug") == key)

def find_duplicate_distractors(q: dict) -> list[str]:
    seen, dupes = set(), []
    for d in q["distractors"]:
        if d["text"] in seen:
            dupes.append(d["text"])
        else:
            seen.add(d["text"])
    return dupes

def dedupe_distractors(q):
    seen = set()
    out = []
    for d in q["distractors"]:
        if d["text"] in seen:
            continue
        seen.add(d["text"])
        out.append(d)
    return out
