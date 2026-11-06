SYLLABUS_PROMPT = """You are designing a {n}-article learning path for the topic "{name}".

Topic description: {description}

Produce EXACTLY {n} articles, ordered from beginner to advanced.

Output format — for each article, EXACTLY this block:

--- A{{index}} ---
SLUG: <lowercase-dash-separated, 2-5 words, unique>
TITLE: <human-readable title, 3-8 words>
SUMMARY: <one sentence, 60-120 chars, explaining what the article covers>

Rules:
- Slug must be ASCII, lowercase, dash-separated, no leading/trailing dash.
- Slugs must be unique within the topic.
- Order matters: first block is easiest, last is hardest.
- No markdown, no code fences, no extra text before or after.
- Do NOT include explanations or comments.
"""

ARTICLE_PROMPT = """Write a self-contained educational article.

Topic: {domain_name}
Article title: {title}
Summary: {summary}

Requirements:
- Plain Markdown. Use ## for sections (no H1, the app renders the title separately).
- 500-800 words.
- Concrete examples, exact commands, real-world specifics.
- Use fenced code blocks with a language tag (```bash, ```python, ```yaml) for commands and config.
- Use LaTeX only for mathematical expressions if relevant: $x^2$ inline, $$...$$ display.
- No fluff ("In this article...", "Let's explore..."). No conclusion paragraph.
- End on a concrete fact, example, or command.

Return only the Markdown content. No preamble, no wrapping code fences.
"""    # из Cell 3

QUESTIONS_PROMPT = """Write {n} quiz questions based on the article below.

Article title: {title}
Topic: {domain_name}

Article:
---
{article_content}
---

For each question, output EXACTLY this block:

--- Q{{index}} ---
Q: <question text, one line, ends with a question mark>
A: <correct answer, one line, <= 60 characters>
D: <distractor 1 text, one line, <= 60 characters>
W: <why distractor 1 is wrong and the correct answer is right, one line, 20-40 words>
D: <distractor 2 text, one line, <= 60 characters>
W: <why distractor 2 is wrong and the correct answer is right, one line, 20-40 words>
D: <distractor 3 text, one line, <= 60 characters>
W: <why distractor 3 is wrong and the correct answer is right, one line, 20-40 words>
D: <distractor 4 text, one line, <= 60 characters>
W: <why distractor 4 is wrong and the correct answer is right, one line, 20-40 words>
H: <hint 1, one line, general direction>
H: <hint 2, one line, narrower>
H: <hint 3, one line, almost the answer>

Rules:
- {n} questions total. Markers Q1, Q2, ..., Q{n}.
- Every field on exactly ONE line. No multi-line values.
- Markers are Q:, A:, D:, W:, H: at the start of a line, followed by a single space.
- Exactly {d} distractors per question, each followed by exactly one W: line.
- Exactly {h} hints per question.
- W: explains why the answer is correct AND why that distractor is wrong, in one or two clauses.
- W: must be fully self-contained. FORBIDDEN openings: "The article...",
  "The text...", "It is stated that...", "As mentioned...", "According to the text...".
  Write "X is Y because Z", NOT "The article says X is Y because Z".
  Assume the reader has never seen any source material.
- H: must be self-contained. No "see the article".
- Correct answer must NOT appear among distractors.
- Distractors must be plausible, not absurd. No "all of the above".
- No markdown, no code fences, no extra text outside the blocks.
"""  # из Cell 4

SYLLABUS_TRANSLATION_PROMPT = """Translate the following quiz-topic article metadata from English to {lang}.

For each article, output EXACTLY this block:

--- A{{index}} ---
SLUG: <keep exactly as in the input>
TITLE: <translated title>
SUMMARY: <translated summary>

Rules:
- Keep the SLUG line identical to the input. Slugs are not translated.
- Translate only TITLE and SUMMARY.
- One line per field. No extra text.
- Preserve order and count.

Input:
{input}
"""  # из Cell 6

ARTICLE_TRANSLATION_PROMPT = """Translate the following Markdown article from English to {lang}.

CRITICAL RULES:
- Preserve ALL Markdown structure: headings (##), lists, **bold**, *italic*, blockquotes, horizontal rules.
- Preserve ALL fenced code blocks (```bash, ```python, ```yaml, ...) — do NOT translate the code inside them.
- Preserve ALL inline code `like this`.
- Preserve ALL LaTeX: $...$ inline, $$...$$ display.
- Preserve URLs, file paths, command names, config keys, and standard English technical terms
  (e.g. "TLS", "DNS", "container", "pod", "process") as-is unless a widely accepted translation exists.
- Do NOT add or remove content. Do NOT add a title.
- Keep paragraph structure identical.

Return ONLY the translated Markdown. No preamble, no wrapping fences.

Source:
---
{source}
---
"""   # из Cell 7

QUESTION_TRANSLATION_PROMPT = """Translate the following quiz questions from English to {lang}.

For each question, output EXACTLY the same block structure as the input,
with markers Q:, A:, D:, W:, H: preserved as-is.

Rules:
- Same number of D: lines, in the same order.
- Same number of W: lines, one after each D:.
- Same number of H: lines.
- One line per field. No multi-line values.
- Translate the content after each marker.
- Keep the same meaning; do not add or remove information.
- Preserve technical terms, command names, file paths, and code in backticks as-is.
- The translated correct answer must NOT appear among translated distractors.
- **CRITICAL**: All 4 D: (distractor) lines MUST have completely unique text. If two English distractors would translate to the same word, you MUST pick a synonym for one of them to keep them distinct.

Input blocks:
{input}
"""  # из Cell 8

CLASSIFY_PROMPT = """You are matching quiz questions to the articles they were likely generated from.

Domain: {domain_name}

Articles in this domain (the slug is the identifier):
{article_list}

Questions:
{question_list}

For each question, output EXACTLY one line in the form:
Q<index>: <article_slug>

Rules:
- <article_slug> must be one of the slugs above, exactly as written.
- Every question must be assigned to exactly one article.
- Output only these lines. No prelude, no commentary, no blank lines.
"""   # из Cell 11

RETRY_QUESTIONS_PROMPT = """Write {n} NEW quiz questions based on the article below.

Article title: {title}
Topic: {domain_name}

Article:
---
{article_content}
---

This is a RETRY. Your previous output for this article failed validation.
Reasons from the previous attempt:
{previous_errors}

Read those reasons carefully and avoid them. In particular:
- Every "why" line must be SELF-CONTAINED. Do NOT write "the article says" or
  "the text states". Instead of "The article states that X is Y", write
  "X is Y because ...". Assume the reader has not seen the article.
- Each D: line must be immediately followed by exactly one W: line.
- Exactly {d} distractors per question.
- Exactly {n} questions total in this response.

For each question, output EXACTLY this block:

--- Q{{index}} ---
Q: <question text, one line, ends with a question mark>
A: <correct answer, one line, <= 60 characters>
D: <distractor 1 text, one line, <= 60 characters>
W: <self-contained explanation, 20-40 words, no reference to "the article">
... (repeat D/W for {d} distractors total)
H: <hint 1, one line>
H: <hint 2, one line>
H: <hint 3, one line>

Rules:
- Every field on exactly ONE line. No multi-line values.
- Markers are Q:, A:, D:, W:, H: at the start of a line, followed by a single space.
- Correct answer must NOT appear among distractors.
- Do NOT repeat the questions listed under "reasons from the previous attempt".
- No markdown, no code fences, no extra text outside the blocks.
"""  # из Cell 12

SINGLE_Q_PROMPT = """Write ONE self-contained quiz question about the topic below.

Topic: {domain_name}
Article title: {title}

Reference material (do not quote it, do not refer to it):
---
{article_content}
---

Output EXACTLY this block, and nothing else:

--- Q1 ---
Q: <question text, one line, ends with ?>
A: <correct answer, one line, <= 60 characters>
D: <distractor 1, one line, <= 60 characters>
W: <why distractor 1 is wrong and the answer is right, 20-40 words>
D: <distractor 2, one line, <= 60 characters>
W: <why distractor 2 is wrong and the answer is right, 20-40 words>
D: <distractor 3, one line, <= 60 characters>
W: <why distractor 3 is wrong and the answer is right, 20-40 words>
D: <distractor 4, one line, <= 60 characters>
W: <why distractor 4 is wrong and the answer is right, 20-40 words>
H: <hint 1, general direction>
H: <hint 2, narrower>
H: <hint 3, almost the answer>

Rules (violation = the question is rejected):
- Do not write "the article", "the text", "as mentioned". Every W: must stand alone.
- Every field on its own single line.
- Exactly 4 D: lines and exactly 4 W: lines, alternating D then W.
- Exactly 3 H: lines.
- Correct answer must not appear among distractors.
"""   # из Cell 14 предыдущего шага

DOMAIN_META_PROMPT = """Translate this metadata from English to {lang}.

Output EXACTLY:
NAME: <translated>
DESCRIPTION: <translated>

Input:
NAME: {name}
DESCRIPTION: {description}
"""

CONSTRUCT_QUESTIONS_PROMPT = """Write {n} quiz questions based on the article below.

Article title: {title}
Topic: {domain_name}

Article:
---
{article_content}
---

For each question, output EXACTLY this block:
--- Q{{index}} ---
Q: <question text, ends with ?>
T: <token 1>
T: <token 2>
...
DT: <distractor token 1>
DT: <distractor token 2>
H: <hint 1>
H: <hint 2>
H: <hint 3>

Rules:
- Output the correct answer as a sequence of T: lines, one word/phrase per line.
- Output exactly 2-4 distractor tokens as DT: lines.
- The correct answer must NOT include DT: tokens.
- Exactly {h} hints per question as H: lines.
- No markdown, no code fences, no extra text outside the blocks.
"""

CONSTRUCT_TRANSLATION_PROMPT = """Translate the following CONSTRUCT question from English to {lang}.
Input:
Q: {question}
{tokens_block}
{distractors_block}
{hints_block}
Output EXACTLY:
Q: <translated question>
T: <translated token 1>
...
DT: <translated distractor 1>
...
H: <translated hint 1>
...
Rules:
- The number of T: and DT: lines MUST match the input exactly.
- Translate tokens in the context of the whole sentence.
"""
