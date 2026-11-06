from pathlib import Path
import json
from uuid import uuid4

def make_output_tree(tmp_path, *, domain="alpha", lang="en",
                     articles=3, questions_per_article=5) -> Path:
    """Minimal output/ tree for selection engine tests. Returns output_root."""
    out = tmp_path / "output"

    (out / "syllabus" / lang).mkdir(parents=True, exist_ok=True)
    syllabus_items = [
        {"order": i, "slug": f"art-{i}", "title": f"Art {i}", "summary": f"Summary {i}", "uuid": str(uuid4())}
        for i in range(1, articles + 1)
    ]
    (out / "syllabus" / lang / f"{domain}.json").write_text(json.dumps(syllabus_items), encoding="utf-8")

    (out / "datasets" / lang).mkdir(parents=True, exist_ok=True)
    qs = []
    for i in range(1, articles + 1):
        for q_idx in range(questions_per_article):
            qs.append({
                "uuid": str(uuid4()),
                "text": f"Question {q_idx} for art-{i}",
                "answer": f"Answer {q_idx}",
                "distractors": ["D1", "D2", "D3"],
                "hints": ["Hint"],
                "difficulty": 1,
                "relatedArticleSlug": f"{domain}/art-{i}",
            })
    (out / "datasets" / lang / f"{domain}.json").write_text(json.dumps(qs), encoding="utf-8")
    (out / "datasets" / lang / "domains.json").write_text(
        json.dumps([{"slug": domain, "name": "Alpha", "description": ""}]),
        encoding="utf-8",
    )
    (out / "datasets" / lang / "campaigns.json").write_text("[]", encoding="utf-8")

    (out / "knowledge" / lang / domain).mkdir(parents=True, exist_ok=True)
    index_items = []
    for i in range(1, articles + 1):
        slug = f"art-{i}"
        md_path = out / "knowledge" / lang / domain / f"{slug}.md"
        md_path.write_text(f"# Article {i}\n" + ("x" * 2500), encoding="utf-8")
        index_items.append({
            "slug": f"{domain}/{slug}",
            "uuid": str(uuid4()),
            "domainSlug": domain,
            "title": f"Title {i}",
            "description": f"Desc {i}",
            "path": f"{domain}/{slug}.md",
            "order": i,
        })
    (out / "knowledge" / lang / "index.json").write_text(json.dumps(index_items), encoding="utf-8")

    return out
