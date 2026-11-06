from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(s: str) -> str:
        return s

log = logging.getLogger(__name__)

DISTRACTOR_WHY_FORBIDDEN = re.compile(
    r"\b("
    r"the article|the text|the passage|the summary|the material|"
    r"as mentioned|as stated|as described|as noted|as explained|"
    r"according to (the|this)|see above|the author"
    r")\b",
    re.I,
)

_BLOCK_HEADER = re.compile(r"---\s*[AQ](\d+)\s*---")
_FIELD_LINE   = re.compile(r"^([A-Z]+):\s*(.+)$")
_FIELD_LINE_Q = re.compile(r"^([QADWHT]+):\s*(.+)$")


@dataclass(frozen=True)
class ParseDiagnostic:
    block_index: int
    errs: tuple[str, ...]


def parse_syllabus(raw: str) -> list[dict]:
    """Разбирает блочный формат --- A<n> --- с полями SLUG/TITLE/SUMMARY."""
    blocks = re.split(r"---\s*A(\d+)\s*---", raw)
    items: list[dict] = []
    for i in range(1, len(blocks), 2):
        idx = int(blocks[i])
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        fields: dict[str, str] = {}
        for line in body.splitlines():
            m = _FIELD_LINE.match(line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        if not all(k in fields for k in ("SLUG", "TITLE", "SUMMARY")):
            continue
        items.append({
            "order":   idx,
            "slug":    fields["SLUG"].lower(),
            "title":   fields["TITLE"],
            "summary": fields["SUMMARY"],
        })
    items.sort(key=lambda x: x["order"])
    return items


def parse_questions(raw: str, expected: int | None = None) -> tuple[list[dict], list[ParseDiagnostic]]:
    """Разбирает блочный формат --- Q<n> --- с маркерами Q/A/D/W/H/T/DT.

    expected — если задано, ограничивает число блоков.
    Возвращает (parsed, diagnostics). Невалидные печатает в stdout и возвращает в diagnostics.
    """
    blocks = re.split(r"---\s*Q(\d+)\s*---", raw)
    parsed: list[dict] = []
    diagnostics: list[ParseDiagnostic] = []
    for i in range(1, len(blocks), 2):
        if expected is not None and len(parsed) >= expected:
            break
        idx = int(blocks[i])
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        lines = [l.rstrip() for l in body.splitlines() if l.strip()]

        q_text = None
        answer = None
        distractor_pairs: list[list[str]] = []
        t_lines: list[str] = []
        dt_lines: list[str] = []
        hints: list[str] = []

        for line in lines:
            m = _FIELD_LINE.match(line)
            if not m:
                continue
            code, value = m.group(1), m.group(2).strip()
            if code == "Q":
                q_text = value
            elif code == "A":
                answer = value
            elif code == "D":
                distractor_pairs.append([value, ""])
            elif code == "W":
                if distractor_pairs and distractor_pairs[-1][1] == "":
                    distractor_pairs[-1][1] = value
            elif code == "T":
                t_lines.append(value)
            elif code == "DT":
                dt_lines.append(value)
            elif code == "H":
                hints.append(value)

        if t_lines:
            type_val = "construct"
            tokens = [{"text": t} for t in t_lines]
            distractor_tokens = [{"text": dt} for dt in dt_lines]
            solution = list(range(len(tokens)))
            answer_val = " ".join(t_lines)
            payload_json = json.dumps({"tokens": tokens, "solution": solution, "distractorTokens": distractor_tokens})

            errs: list[str] = []
            if q_text is None: errs.append("no Q")
            if not tokens: errs.append("no T")
            from .config import HINTS_PER_QUESTION
            if not (2 <= len(distractor_tokens) <= 4):
                errs.append(f"{len(distractor_tokens)} distractor tokens (need 2..4)")
            if len(hints) != HINTS_PER_QUESTION:
                errs.append(f"{len(hints)} hints")

            if errs:
                print(f"  !! Q{idx} rejected: {', '.join(errs)}")
                log.warning("parse_reject_block", extra={"block_index": idx, "errs": errs})
                diagnostics.append(ParseDiagnostic(block_index=idx, errs=tuple(errs)))
                continue

            parsed.append({
                "text": q_text,
                "type": type_val,
                "tokens": tokens,
                "solution": solution,
                "distractorTokens": distractor_tokens,
                "answer": answer_val,
                "payloadJson": payload_json,
                "hints": hints,
            })
            continue

        seen_texts = set()
        unique_pairs = []
        for d_text, w_text in distractor_pairs:
            if d_text not in seen_texts:
                seen_texts.add(d_text)
                unique_pairs.append([d_text, w_text])
        distractor_pairs = unique_pairs

        errs: list[str] = []
        if q_text is None: errs.append("no Q")
        if answer is None: errs.append("no A")
        # количества D/W/H вынесены в config; здесь — мягкая проверка
        from .config import DISTRACTORS_PER_QUESTION, HINTS_PER_QUESTION
        if not (2 <= len(distractor_pairs) <= 6):
            errs.append(f"{len(distractor_pairs)} distractors (need 2..6)")
        if any(w == "" for _, w in distractor_pairs):
            errs.append("missing W")
        if len(hints) != HINTS_PER_QUESTION:
            errs.append(f"{len(hints)} hints")
        if answer is not None and any(d == answer for d, _ in distractor_pairs):
            errs.append("answer appears in distractors")
        for _, w in distractor_pairs:
            if DISTRACTOR_WHY_FORBIDDEN.search(w):
                errs.append(f"why references article: {w[:40]!r}")

        if errs:
            print(f"  !! Q{idx} rejected: {', '.join(errs)}")
            log.warning("parse_reject_block", extra={"block_index": idx, "errs": errs})
            diagnostics.append(ParseDiagnostic(block_index=idx, errs=tuple(errs)))
            continue

        parsed.append({
            "text": q_text,
            "type": "select",
            "answer": answer,
            "distractors": [{"text": d, "why": w} for d, w in distractor_pairs],
            "hints": hints,
        })

    return parsed, diagnostics


def parse_domain_list(raw: str) -> list[dict]:
    """Разбирает список доменов из Markdown текста."""
    header_regex = re.compile(r"^#+\s*(\d+)\.?\s*(.*)$", re.MULTILINE)
    matches = list(header_regex.finditer(raw))
    items: list[dict] = []
    
    for idx, m in enumerate(matches):
        order = int(m.group(1))
        header_title = m.group(2).strip().strip("`'\"")
        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        body = raw[body_start:body_end]
        
        slug = ""
        title = header_title
        desc_lines = []
        in_desc = False
        
        for line in body.splitlines():
            line_str = line.strip()
            if line_str.lower().startswith("slug:"):
                slug = line_str[5:].strip().strip("`'\"")
                in_desc = False
            elif line_str.lower().startswith("title:"):
                t_val = line_str[6:].strip().strip("`'\"")
                if t_val:
                    title = t_val
                in_desc = False
            elif line_str.lower().startswith("description:"):
                desc_val = line_str[12:].strip().strip("`'\"")
                if desc_val:
                    desc_lines.append(desc_val)
                in_desc = True
            elif in_desc:
                if line_str:
                    desc_lines.append(line_str)
            else:
                if line_str and not slug:
                    desc_lines.append(line_str)
                    in_desc = True
                    
        description = " ".join(desc_lines).strip()
        
        if not slug and title:
            ascii_title = unidecode(title)
            slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
            
        if not slug:
            slug = f"domain-{order}"
            
        items.append({
            "order": order,
            "slug": slug.lower(),
            "title": title,
            "description": description,
        })
        
    items.sort(key=lambda x: x["order"])
    return items
