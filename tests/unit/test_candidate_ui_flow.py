from pathlib import Path

WEB_ROOT = Path(__file__).parents[2] / "src" / "agentic_interviewer" / "web"


def test_resume_submission_moves_to_device_check_before_intake_finishes():
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    handler = script[
        script.index("async function prepareInterview") : script.index(
            "async function inspectPermissions"
        )
    ]

    assert handler.index('setStage("device")') < handler.index('await api("/api/candidate/intakes"')
    assert "state.intakePending" in handler
    assert 'elements.preparedCount.textContent = "Ready when you are"' in handler


def test_candidate_copy_does_not_expose_question_preparation_in_preflight():
    markup = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    candidate_copy = f"{markup}\n{script}"
    assert "Preparing questions" not in candidate_copy
    assert "questions prepared" not in candidate_copy
    assert "Degraded question planning" not in candidate_copy


def test_voice_turn_has_silence_nudge_and_hard_answer_deadline():
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert "const SILENCE_NUDGE_MS = 12000" in script
    assert "const ANSWER_LIMIT_MS = 90000" in script
    assert "cue=silence_nudge" in script
    assert 'submitAnswer("[answer time expired]", false)' in script
