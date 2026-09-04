import re

from agentic_interviewer.domain.models import CandidateIntent, IntentDecision, TextSpan


class GuardrailPolicy:
    """Versioned deterministic baseline; a semantic classifier may be added later."""

    version = "guardrails-v2"
    _rules: tuple[tuple[str, CandidateIntent, re.Pattern[str]], ...] = (
        (
            "stop",
            CandidateIntent.STOP_OR_WITHDRAW,
            re.compile(r"\b(stop|withdraw|end interview)\b", re.I),
        ),
        (
            "repeat",
            CandidateIntent.REPEAT_REQUEST,
            re.compile(r"\b(repeat|say that again|ask (?:me )?that again)\b", re.I),
        ),
        (
            "clarify",
            CandidateIntent.CLARIFICATION_REQUEST,
            re.compile(
                r"\b(clarify|what do you mean|(?:can|could|would) you "
                r"(?:please )?(?:explain|clarify|rephrase|elaborate)|"
                r"what (?:is|are|does) (?:the )?(?:job )?requirement(?: area)?\b|"
                r"what (?:is|are) (?:requirement|area)\s+(?:\d+|one|two|three|four|five))",
                re.I,
            ),
        ),
        (
            "technical_problem",
            CandidateIntent.TECHNICAL_PROBLEM,
            re.compile(
                r"\b(my (microphone|mic|audio|connection) (is )?"
                r"(not working|isn't working|has failed)|"
                r"(i )?(can't|cannot) hear (you|the audio)|"
                r"audio (is )?(cutting out|not working)|"
                r"connection (dropped|was lost))\b",
                re.I,
            ),
        ),
        (
            "answer",
            CandidateIntent.ANSWER_SEEKING,
            re.compile(r"\b(right answer|answer.*looking for|tell me the answer)\b", re.I),
        ),
        (
            "inject",
            CandidateIntent.PROMPT_INJECTION,
            re.compile(r"\b(ignore (all|your|previous)|system prompt|reveal.*prompt)\b", re.I),
        ),
        (
            "coerce",
            CandidateIntent.COERCION_OR_MANIPULATION,
            re.compile(r"\b(mark me|give me.*score|pass me)\b", re.I),
        ),
        (
            "refusal_or_skip",
            CandidateIntent.REFUSAL_OR_SKIP,
            re.compile(
                r"\b(?:i (?:do not|don't) know|please move on|move (?:on|to the next)|"
                r"next question|skip (?:it|this|the question))\b|"
                r"^\[answer time expired\]$|"
                r"^(?:(?:no+|nope)[.!?,\s]*)+$|"
                r"^(?:i(?:'m| am) not sure|pass|skip(?: it| this)?|"
                r"cannot answer|can't answer)[.!?,\s]*$",
                re.I,
            ),
        ),
        (
            "abusive_language",
            CandidateIntent.ABUSIVE_OR_UNSAFE,
            re.compile(
                r"\b(?:fuck(?:ing)?|shit(?:ty)?|bullshit|bitch|asshole|motherfucker)\b",
                re.I,
            ),
        ),
    )

    _acknowledgement = re.compile(
        r"^(?:hi|hello|hey|okay|ok|yeah|yes|right|sure|uh+|um+|hmm+)(?:[,.!\s]+"
        r"(?:hi|hello|hey|okay|ok|yeah|yes|right|sure|uh+|um+|hmm+))*[.!?,\s]*$",
        re.I,
    )
    _word = re.compile(r"[A-Za-z][A-Za-z'-]*")

    def classify(self, text: str) -> IntentDecision:
        stripped = text.strip()
        if not stripped:
            return IntentDecision(
                primary_intent=CandidateIntent.UNCERTAIN,
                confidence=1,
                matched_rule_ids=["empty"],
                proposed_action="clarify",
            )
        for rule_id, intent, pattern in self._rules:
            match = pattern.search(stripped)
            if match:
                action = self._action_for(intent)
                return IntentDecision(
                    primary_intent=intent,
                    confidence=1,
                    matched_rule_ids=[rule_id],
                    evidence_spans=[
                        TextSpan(start=match.start(), end=match.end(), text=match.group())
                    ],
                    proposed_action=action,
                )
        if self._looks_unintelligible(stripped):
            return IntentDecision(
                primary_intent=CandidateIntent.UNINTELLIGIBLE,
                confidence=1,
                matched_rule_ids=["unintelligible"],
                proposed_action="clarify",
            )
        if self._acknowledgement.fullmatch(stripped):
            return IntentDecision(
                primary_intent=CandidateIntent.INSUFFICIENT_ANSWER,
                confidence=1,
                matched_rule_ids=["acknowledgement_only"],
                proposed_action="clarify",
            )
        if self._looks_like_candidate_question(stripped):
            return IntentDecision(
                primary_intent=CandidateIntent.CANDIDATE_QUESTION,
                confidence=0.9,
                matched_rule_ids=["candidate_question"],
                proposed_action="clarify",
            )
        words = self._word.findall(stripped)
        if len(words) < 4:
            return IntentDecision(
                primary_intent=CandidateIntent.INSUFFICIENT_ANSWER,
                confidence=0.9,
                matched_rule_ids=["too_short"],
                proposed_action="clarify",
            )
        return IntentDecision(
            primary_intent=CandidateIntent.ANSWER,
            confidence=1,
            proposed_action="allow",
        )

    @classmethod
    def _looks_unintelligible(cls, text: str) -> bool:
        # Realtime STT can emit long runs of isolated letters or repeated tokens.
        if re.fullmatch(r"(?:[A-Za-z][\s-]+){7,}[A-Za-z]", text.strip()):
            return True
        tokens = cls._word.findall(text)
        if len(tokens) >= 8 and sum(len(token) == 1 for token in tokens) / len(tokens) >= 0.7:
            return True
        if len(tokens) >= 8 and len({token.casefold() for token in tokens}) <= 2:
            return True
        if len(tokens) >= 5:
            normalized = [token.casefold() for token in tokens]
            most_common = max(normalized.count(token) for token in set(normalized))
            if most_common / len(normalized) >= 0.55:
                return True
        compact = re.sub(r"[^A-Za-z]", "", text)
        return len(text) >= 24 and bool(compact) and len(compact) / len(text) < 0.35

    @staticmethod
    def _looks_like_candidate_question(text: str) -> bool:
        return bool(
            text.endswith("?")
            or re.match(
                r"^(?:(?:what|why|who|where|when|how)\s+"
                r"(?:is|are|was|were|do|does|did|would|could|can|will)|"
                r"(?:do|does|did|is|are|can|could|would|will)\b)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _action_for(intent: CandidateIntent) -> str:
        if intent is CandidateIntent.STOP_OR_WITHDRAW:
            return "stop"
        if intent in {
            CandidateIntent.REPEAT_REQUEST,
            CandidateIntent.CLARIFICATION_REQUEST,
            CandidateIntent.CANDIDATE_QUESTION,
            CandidateIntent.TECHNICAL_PROBLEM,
            CandidateIntent.INSUFFICIENT_ANSWER,
            CandidateIntent.REFUSAL_OR_SKIP,
            CandidateIntent.UNINTELLIGIBLE,
        }:
            return "clarify"
        return "redirect"
