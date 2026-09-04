from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*", re.IGNORECASE)
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bresume (?:topic|claim)\s*\d*\b", re.IGNORECASE),
    re.compile(r"\b(?:job )?requirement area\s*\d*\b", re.IGNORECASE),
    re.compile(r"\bchoose (?:a|one) relevant (?:resume )?claim\b", re.IGNORECASE),
    re.compile(r"\btopic\s+\d+\b", re.IGNORECASE),
)
_DEPENDENT_REFERENCE_RE = re.compile(
    r"\b(?:the above|as mentioned|this requirement|this claim|that example|earlier example)\b",
    re.IGNORECASE,
)
_OPEN_PROMPT_RE = re.compile(
    r"(?:^|[.!]\s+)(?:please\s+)?(?:briefly\s+)?"
    r"(?:describe|discuss|explain|give|introduce|share|summarize|tell|walk)\b",
    re.IGNORECASE,
)
_CONTACT_RE = re.compile(
    r"(?:@|https?://|www\.|(?:github|gitlab|linkedin)\.com|"
    r"\b[a-z0-9.-]+\.(?:com|dev|io|net|org)(?:[/\s]|$)|\+?\d[\d ()-]{7,})",
    re.IGNORECASE,
)
_SECTION_PREFIX_RE = re.compile(
    r"^(?:summary|experience|education|skills|projects|certifications|achievements|"
    r"employment|profile|requirements)\s*[:|]\s*",
    re.IGNORECASE,
)
_DANGLING_END_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_SOURCE_WRAPPER_RE = re.compile(
    r"\b(?:your resume (?:states|says|mentions)|in the resume (?:example|statement)|"
    r"the (?:resume )?(?:statement|excerpt|line)|the (?:job )?(?:requirement|excerpt))\b",
    re.IGNORECASE,
)
_HEADER_WORDS = frozenset(
    {
        "summary",
        "experience",
        "education",
        "skills",
        "projects",
        "certifications",
        "achievements",
        "employment",
        "profile",
        "requirements",
    }
)
_ACTION_WORDS = frozenset(
    {
        "built",
        "created",
        "delivered",
        "designed",
        "developed",
        "implemented",
        "improved",
        "launched",
        "led",
        "managed",
        "migrated",
        "operated",
        "owned",
        "reduced",
        "shipped",
    }
)
_JOB_ACTION_FORMS = {
    "build": "building",
    "create": "creating",
    "deliver": "delivering",
    "design": "designing",
    "develop": "developing",
    "implement": "implementing",
    "lead": "leading",
    "manage": "managing",
    "operate": "operating",
    "own": "owning",
}
_LOW_SIGNAL_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "with",
        "would",
        "you",
        "your",
    }
)


class SourceItem(BaseModel):
    """A bounded source excerpt that a planned question may rely on."""

    id: str
    kind: Literal["resume_claim", "job_requirement"]
    text: str = Field(min_length=3, max_length=600)
    source: Literal["resume", "job_details"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    line: int = Field(ge=1)


class PlannedQuestion(BaseModel):
    text: str = Field(min_length=1, max_length=1_000)
    source_item_id: str | None = None


class GroundedQuestionBank(BaseModel):
    introduction: list[PlannedQuestion] = Field(default_factory=list, max_length=3)
    resume: list[PlannedQuestion] = Field(default_factory=list, max_length=20)
    job: list[PlannedQuestion] = Field(default_factory=list, max_length=20)


class QuestionQualityError(ValueError):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("question bank failed quality checks: " + "; ".join(violations))


def extract_resume_claims(text: str, *, limit: int = 30) -> list[SourceItem]:
    return _extract_items(text, kind="resume_claim", source="resume", limit=limit)


def extract_job_requirements(text: str, *, limit: int = 30) -> list[SourceItem]:
    lines = text.splitlines()
    has_bullets = any(re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", line) for line in lines)
    candidate_text = (
        "\n".join(
            line if re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", line) else "" for line in lines
        )
        if has_bullets
        else text
    )
    return _extract_items(
        candidate_text,
        kind="job_requirement",
        source="job_details",
        limit=limit,
        original_text=text,
    )


def _extract_items(
    text: str,
    *,
    kind: Literal["resume_claim", "job_requirement"],
    source: Literal["resume", "job_details"],
    limit: int,
    original_text: str | None = None,
) -> list[SourceItem]:
    original = original_text or text
    pieces: list[tuple[str, int, int, int]] = []
    search_from = 0
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        cleaned_line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", "", raw_line).strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\s*[;]\s*", cleaned_line):
            cleaned = re.sub(r"\s+", " ", sentence).strip(" -\t")
            words = _WORD_RE.findall(cleaned)
            looks_like_name = (
                kind == "resume_claim"
                and line_number == 1
                and 1 <= len(words) <= 4
                and all(word[:1].isupper() for word in cleaned.split())
                and not (_ACTION_WORDS & {word.casefold() for word in words})
            )
            if (
                len(words) < 2
                or cleaned.casefold() in _HEADER_WORDS
                or _CONTACT_RE.search(cleaned)
                or _looks_like_heading(cleaned, words)
                or words[-1].casefold() in _DANGLING_END_WORDS
                or looks_like_name
            ):
                continue
            start = original.find(cleaned, search_from)
            if start < 0:
                start = original.find(cleaned)
            if start < 0:
                start = 0
            end = start + len(cleaned)
            search_from = end
            pieces.append((cleaned[:600], start, end, line_number))

    seen: set[str] = set()
    items: list[SourceItem] = []
    prefix = "resume-claim" if kind == "resume_claim" else "job-requirement"
    for cleaned, start, end, line_number in pieces:
        normalized = _normalize_text(cleaned)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            SourceItem(
                id=f"{prefix}-{len(items) + 1}",
                kind=kind,
                text=cleaned,
                source=source,
                start=start,
                end=end,
                line=line_number,
            )
        )
        if len(items) >= limit:
            break
    return items


def validate_question_bank(
    bank: GroundedQuestionBank,
    *,
    resume_items: list[SourceItem],
    job_items: list[SourceItem],
    expected_counts: dict[str, int],
) -> None:
    violations: list[str] = []
    collections = {
        "introduction": bank.introduction,
        "resume": bank.resume,
        "job": bank.job,
    }
    item_maps = {
        "resume": {item.id: item for item in resume_items},
        "job": {item.id: item for item in job_items},
    }
    actual_counts = {name: len(items) for name, items in collections.items()}
    if actual_counts != expected_counts:
        violations.append(f"wrong allocation: expected {expected_counts}, received {actual_counts}")

    all_questions: list[tuple[str, int, PlannedQuestion]] = []
    for group, questions in collections.items():
        for index, question in enumerate(questions):
            label = f"{group}[{index}]"
            text = re.sub(r"\s+", " ", question.text).strip()
            all_questions.append((label, index, question))
            is_open_prompt = text.endswith("?") or bool(_OPEN_PROMPT_RE.search(text))
            if len(_WORD_RE.findall(text)) < 7 or not is_open_prompt:
                violations.append(f"{label} is not a complete, open question")
            if any(pattern.search(text) for pattern in _PLACEHOLDER_PATTERNS):
                violations.append(f"{label} contains placeholder wording")
            if _DEPENDENT_REFERENCE_RE.search(text):
                violations.append(f"{label} is not understandable on its own")
            if group == "introduction":
                if question.source_item_id is not None:
                    violations.append(f"{label} must not claim source grounding")
                continue
            source_item = item_maps[group].get(question.source_item_id or "")
            if source_item is None:
                violations.append(f"{label} does not reference a valid {group} source item")
                continue
            if _SOURCE_WRAPPER_RE.search(text) or _quotes_entire_source(text, source_item.text):
                violations.append(f"{label} presents source content as a quoted fragment")
            overlap = _meaningful_tokens(text) & _meaningful_tokens(source_item.text)
            if not overlap:
                violations.append(f"{label} does not mention its referenced source content")

    for left_index, (left_label, _, left) in enumerate(all_questions):
        left_tokens = _meaningful_tokens(left.text)
        for right_label, _, right in all_questions[left_index + 1 :]:
            right_tokens = _meaningful_tokens(right.text)
            if _normalize_text(left.text) == _normalize_text(right.text):
                violations.append(f"{right_label} duplicates {left_label}")
                continue
            union = left_tokens | right_tokens
            if union and len(left_tokens & right_tokens) / len(union) >= 0.82:
                violations.append(f"{right_label} is too similar to {left_label}")

    if violations:
        raise QuestionQualityError(violations)


def build_grounded_fallback_bank(
    *,
    job_title: str,
    resume_text: str,
    job_details: str,
    expected_counts: dict[str, int],
) -> tuple[GroundedQuestionBank, list[SourceItem], list[SourceItem]]:
    resume_items = extract_resume_claims(resume_text)
    job_items = extract_job_requirements(job_details)
    if not resume_items and resume_text.strip():
        resume_excerpt = re.sub(r"\s+", " ", resume_text).strip()[:600]
        resume_items = [
            SourceItem(
                id="resume-claim-1",
                kind="resume_claim",
                text=resume_excerpt,
                source="resume",
                start=0,
                end=len(resume_excerpt),
                line=1,
            )
        ]
    if not job_items and job_details.strip():
        job_excerpt = re.sub(r"\s+", " ", job_details).strip()[:600]
        job_items = [
            SourceItem(
                id="job-requirement-1",
                kind="job_requirement",
                text=job_excerpt,
                source="job_details",
                start=0,
                end=len(job_excerpt),
                line=1,
            )
        ]
    if expected_counts["resume"] and not resume_items:
        raise QuestionQualityError(
            ["resume has no concrete claims suitable for interview questions"]
        )
    if expected_counts["job"] and not job_items:
        raise QuestionQualityError(["job details have no concrete requirements"])

    introduction_templates = (
        "Please introduce yourself and explain why the {title} role is a relevant next step "
        "for you?",
        "Which part of your experience best prepares you for the {title} role, and why?",
        "What would you aim to contribute during your first months in the {title} role?",
    )
    introductions = [
        PlannedQuestion(text=introduction_templates[index].format(title=job_title))
        for index in range(expected_counts["introduction"])
    ]
    resume_templates = (
        "{lead} What did you personally own, and what changed as a result?",
        "{lead} What was the hardest problem, and how did you solve it?",
        "{lead} Which alternative did you reject, and why?",
        "{lead} How did you verify the result or measure success?",
        "{lead} What would you design or execute differently now, and why?",
        "{lead} Which constraint most shaped your approach, and why?",
        "{lead} What failed or nearly failed, and how did you respond?",
        "{lead} How did you test that your solution was reliable?",
        "{lead} How did you collaborate with the people affected by the work?",
        "{lead} What did you do to make the result scale beyond the first use?",
        "{lead} Which security or safety risk did you address personally?",
        "{lead} How did you balance speed, quality, and scope?",
        "{lead} How did you investigate the most important unknown?",
        "{lead} Which technical detail best demonstrates the depth of your contribution?",
        "{lead} How did feedback change a decision you had already made?",
        "{lead} What did you do when the available evidence was incomplete?",
        "{lead} Which cost did you reduce or deliberately accept, and why?",
        "{lead} What did you learn that changed your later work?",
        "{lead} What part of the outcome can be attributed directly to you?",
        "{lead} How would you transfer that experience to a different technical environment?",
    )
    job_templates = (
        "{lead} Tell me about a time you demonstrated that capability in practice?",
        "{lead} How would you approach the work, and what trade-off matters most?",
        "{lead} What failure mode would you plan for first, and why?",
        "{lead} How would you know your implementation was working well in production?",
        "{lead} Describe the most relevant decision you made and the evidence behind it?",
        "{lead} Which constraint would you clarify before designing a solution?",
        "{lead} Describe a credible failure scenario and how you would contain it?",
        "{lead} How would you align the people involved in delivering it?",
        "{lead} What would you test before releasing the work to users?",
        "{lead} How would you balance delivery speed with long-term maintenance?",
        "{lead} Which production signal would tell you to change course?",
        "{lead} How would you investigate an unfamiliar part of the problem?",
        "{lead} What would you deliberately keep simple, and why?",
        "{lead} How would you control cost without weakening the outcome?",
        "{lead} What security or privacy concern deserves attention first?",
        "{lead} How would you explain a difficult trade-off to stakeholders?",
        "{lead} Which assumption would you validate before committing to an architecture?",
        "{lead} How would you recover from an unsuccessful first approach?",
        "{lead} What evidence from your past work is most relevant to this responsibility?",
        "{lead} How would your approach change in a different technical context?",
    )
    resume_questions = _fallback_questions(
        resume_items, expected_counts["resume"], resume_templates, source="resume"
    )
    job_questions = _fallback_questions(
        job_items, expected_counts["job"], job_templates, source="job"
    )
    bank = GroundedQuestionBank(
        introduction=introductions,
        resume=resume_questions,
        job=job_questions,
    )
    validate_question_bank(
        bank,
        resume_items=resume_items,
        job_items=job_items,
        expected_counts=expected_counts,
    )
    return bank, resume_items, job_items


def question_provenance(
    question: PlannedQuestion,
    items: list[SourceItem],
    *,
    generator: str,
) -> dict[str, object]:
    item = next((candidate for candidate in items if candidate.id == question.source_item_id), None)
    if item is None:
        return {"generator": generator}
    return {
        "generator": generator,
        "source_item_id": item.id,
        "source_kind": item.kind,
        "source": item.source,
        "source_span": {"start": item.start, "end": item.end, "line": item.line},
        "source_excerpt": item.text,
    }


def _fallback_questions(
    items: list[SourceItem],
    count: int,
    templates: tuple[str, ...],
    *,
    source: Literal["resume", "job"],
) -> list[PlannedQuestion]:
    return [
        PlannedQuestion(
            text=templates[index % len(templates)].format(
                lead=_source_lead(items[index % len(items)].text, source=source)
            ),
            source_item_id=items[index % len(items)].id,
        )
        for index in range(count)
    ]


def _normalize_text(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.casefold()))


def _meaningful_tokens(text: str) -> set[str]:
    counts = Counter(_WORD_RE.findall(text.casefold()))
    normalized = {word.strip(".-") for word in counts}
    return {word for word in normalized if len(word) >= 3 and word not in _LOW_SIGNAL_WORDS}


def _looks_like_heading(text: str, words: list[str]) -> bool:
    if _SECTION_PREFIX_RE.match(text):
        return True
    action_words = _ACTION_WORDS & {word.casefold() for word in words}
    if "|" in text and len(words) <= 8 and not action_words:
        return True
    title_words = [word for word in re.split(r"\s+", text) if word]
    if (
        len(words) <= 5
        and title_words
        and all(word[:1].isupper() for word in title_words)
        and not action_words
    ):
        return True
    return text.endswith(":") and len(words) <= 8


def _quotes_entire_source(question: str, source_text: str) -> bool:
    escaped = re.escape(source_text.strip().rstrip(".!?"))
    pattern = rf'["\u201c\u201d]\s*{escaped}[\s.!?]*["\u201c\u201d]'
    return bool(re.search(pattern, question, re.IGNORECASE))


def _source_lead(text: str, *, source: Literal["resume", "job"]) -> str:
    statement = re.sub(r"\s+", " ", text).strip().rstrip(".!?")
    first_word, separator, remainder = statement.partition(" ")
    if source == "resume":
        if first_word.casefold() in _ACTION_WORDS and separator:
            return f"You {first_word.casefold()} {remainder}."
        return f"You mention {statement[:1].lower() + statement[1:]}."
    action_form = _JOB_ACTION_FORMS.get(first_word.casefold())
    if action_form and separator:
        return f"This role involves {action_form} {remainder}."
    return f"This role calls for experience with {statement}."
