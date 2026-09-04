from fastapi.testclient import TestClient

from agentic_interviewer.adapters.fake import FakeUnifiedAdapter
from agentic_interviewer.adapters.router import CapabilityRouter, ProviderBundle
from agentic_interviewer.api.app import create_app
from agentic_interviewer.composition.providers import ProviderRuntime
from agentic_interviewer.config import Settings
from agentic_interviewer.security import Principal, TokenService
from agentic_interviewer.services import InterviewService

SMALL_PLAN = {
    "introduction_questions": 1,
    "resume_questions": 1,
    "job_questions": 0,
    "max_company_questions": 1,
    "max_adaptive_questions": 0,
    "max_total_questions": 2,
}


def client() -> TestClient:
    return TestClient(create_app(Settings(app_env="test"), InterviewService()))


def local_model_app(*, max_concurrent: int = 1) -> tuple[TestClient, object]:
    adapter = FakeUnifiedAdapter()
    providers = ProviderRuntime(
        router=CapabilityRouter(
            {
                "local-specialized": ProviderBundle(
                    reasoning=adapter,
                    transcription=adapter,
                    synthesis=adapter,
                )
            },
            default_profile="local-specialized",
        )
    )
    app = create_app(
        Settings(
            app_env="test",
            provider_profile="local-specialized",
            max_concurrent_model_calls=max_concurrent,
            max_concurrent_model_calls_per_tenant=max_concurrent,
        ),
        InterviewService(),
        providers=providers,
    )
    return TestClient(app), app.state.admission


def test_default_api_plan_uses_buffered_one_five_five_allocation():
    api = client()
    created = api.post(
        "/interviews",
        json={
            "job_title": "Backend Engineer",
            "resume_text": "Built Python APIs",
            "job_details": "Build reliable services",
        },
    )

    assert created.status_code == 201
    questions = created.json()["plan"]["questions"]
    assert len(questions) == 11
    assert [item["source"] for item in questions].count("introduction") == 1
    assert [item["source"] for item in questions].count("resume") == 5
    assert [item["source"] for item in questions].count("job") == 5


def test_api_rejects_question_budget_above_organization_limit():
    api = client()
    response = api.post(
        "/interviews",
        json={
            "job_title": "Backend Engineer",
            "resume_text": "Built Python APIs",
            "job_details": "Build reliable services",
            "planning_controls": {"max_total_questions": 21},
        },
    )

    assert response.status_code == 422
    assert "organization limit" in response.json()["detail"]


def test_api_fails_closed_when_required_company_questions_exceed_capacity():
    api = client()
    response = api.post(
        "/interviews",
        json={
            "job_title": "Backend Engineer",
            "resume_text": "Built Python APIs",
            "job_details": "Build reliable services",
            "company_questions": ["Required one", "Required two"],
            "planning_controls": {
                "introduction_questions": 1,
                "resume_questions": 1,
                "job_questions": 0,
                "company_question_policy": "extend_budget",
                "max_company_questions": 2,
                "max_adaptive_questions": 0,
                "max_total_questions": 2,
            },
        },
    )

    assert response.status_code == 409
    assert "append capacity" in response.json()["detail"]


def test_health_and_text_interview_review_flow():
    api = client()
    assert api.get("/health").json() == {"status": "ok", "environment": "test"}
    created = api.post(
        "/interviews",
        json={
            "job_title": "Backend Engineer",
            "resume_text": "Built Python APIs",
            "job_details": "Build reliable services",
            "planning_controls": SMALL_PLAN,
        },
    )
    assert created.status_code == 201
    interview_id = created.json()["interview_id"]
    setup = api.post(
        f"/interviews/{interview_id}/setup-approval",
        json={"reviewer": "recruiter@example.test", "reason": "Plan and rubric verified"},
    )
    assert setup.json()["status"] == "ready"
    assert api.post(f"/interviews/{interview_id}/start").status_code == 200
    assert (
        api.post(
            f"/interviews/{interview_id}/answers", json={"text": "I built an API."}
        ).status_code
        == 200
    )
    completed = api.post(
        f"/interviews/{interview_id}/answers",
        json={"text": "I fixed a production bottleneck."},
    )
    assert completed.json()["status"] == "completed"
    reviewed = api.post(
        f"/interviews/{interview_id}/review",
        json={"reviewer": "reviewer@example.test", "reason": "Evidence verified"},
    )
    assert reviewed.json()["status"] == "approved"
    exported = api.post(
        f"/interviews/{interview_id}/exports",
        json={"idempotency_key": "export-key-0001"},
    )
    assert exported.status_code == 200
    assert exported.json()["assessment"]["approved"] is True
    duplicate = api.post(
        f"/interviews/{interview_id}/exports",
        json={"idempotency_key": "export-key-0001"},
    )
    assert duplicate.json() == exported.json()


def test_missing_and_invalid_lifecycle_errors():
    api = client()
    assert api.get("/interviews/missing").status_code == 404
    created = api.post(
        "/interviews",
        json={"job_title": "Role", "resume_text": "Resume", "job_details": "Details"},
    ).json()
    interview_id = created["interview_id"]
    response = api.post(f"/interviews/{interview_id}/answers", json={"text": "Too early"})
    assert response.status_code == 409


def test_model_capacity_rejection_is_explicit_instead_of_falling_back():
    api, admission = local_model_app(max_concurrent=1)
    created = api.post(
        "/interviews",
        json={
            "job_title": "Backend Engineer",
            "resume_text": "Built Python APIs",
            "job_details": "Build reliable services",
            "planning_controls": SMALL_PLAN,
        },
    ).json()
    interview_id = created["interview_id"]
    api.post(
        f"/interviews/{interview_id}/setup-approval",
        json={"reviewer": "recruiter", "reason": "verified"},
    )
    api.post(f"/interviews/{interview_id}/start")

    with admission.lease("default"):
        rejected = api.post(
            f"/interviews/{interview_id}/answers",
            json={"text": "I built and operated the service."},
        )

    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "global_concurrency"
    assert api.get(f"/interviews/{interview_id}").json()["answers"] == []


def test_setup_correction_transcript_correction_and_review_override():
    api = client()
    created = api.post(
        "/interviews",
        json={
            "job_title": "Role",
            "resume_text": "Old fact",
            "job_details": "Requirement",
            "planning_controls": SMALL_PLAN,
        },
    ).json()
    interview_id = created["interview_id"]
    corrected = api.patch(
        f"/interviews/{interview_id}/source-claims",
        json={
            "profile": "resume",
            "claim_id": "resume-claim-1",
            "value": "Correct fact",
            "actor": "recruiter@example.test",
        },
    )
    assert corrected.json()["resume_profile"]["claims"][0]["value"] == "Correct fact"
    api.post(
        f"/interviews/{interview_id}/setup-approval",
        json={"reviewer": "recruiter@example.test", "reason": "Verified"},
    )
    api.post(f"/interviews/{interview_id}/start")
    first = api.post(
        f"/interviews/{interview_id}/answers", json={"text": "I built the service."}
    ).json()
    turn_id = first["answers"][0]["id"]
    transcript = api.patch(
        f"/interviews/{interview_id}/transcript",
        json={"turn_id": turn_id, "corrected_text": "I built two services.", "reason": "STT"},
    )
    assert transcript.json()["answers"][0]["corrected_text"] == "I built two services."
    completed = api.post(
        f"/interviews/{interview_id}/answers", json={"text": "I resolved an outage."}
    ).json()
    evidence_ids = [item["id"] for item in completed["evidence"]]
    overridden = api.post(
        f"/interviews/{interview_id}/review/override",
        json={
            "reviewer": "reviewer@example.test",
            "reason": "Calibrated against anchors",
            "competency_scores": [
                {
                    "competency_id": "experience",
                    "score": 3,
                    "evidence_ids": evidence_ids,
                    "rationale": "Two cited examples meet the anchor.",
                }
            ],
        },
    )
    assert overridden.status_code == 200
    assert overridden.json()["assessment"]["weighted_score"] == 3
    appealed = api.post(
        f"/interviews/{interview_id}/appeals", json={"reason": "Please review my correction."}
    )
    assert appealed.status_code == 202
    assert appealed.json()["appeals"][0]["status"] == "pending_human_review"


def test_fake_voice_routes_share_the_text_workflow_and_record_usage():
    api = client()
    created = api.post(
        "/interviews",
        json={"job_title": "Role", "resume_text": "Resume", "job_details": "Details"},
    ).json()
    interview_id = created["interview_id"]
    api.post(
        f"/interviews/{interview_id}/setup-approval",
        json={"reviewer": "recruiter", "reason": "verified"},
    )
    started = api.post(f"/interviews/{interview_id}/start").json()
    speech = api.post(
        f"/interviews/{interview_id}/speech",
        headers={"Idempotency-Key": "speech-key-0001"},
    )
    assert speech.status_code == 200
    assert speech.content == started["last_response"].encode()
    answered = api.post(
        f"/interviews/{interview_id}/audio-answers",
        params={"audio_format": "wav"},
        headers={
            "Idempotency-Key": "audio-key-0001",
            "Content-Type": "application/octet-stream",
        },
        content=b"I built the API.",
    )
    assert answered.status_code == 200
    current = api.get(f"/interviews/{interview_id}").json()
    assert {item["capability"] for item in current["usage"]} == {
        "synthesis",
        "transcription",
    }


def test_signed_roles_and_tenant_boundary_are_enforced():
    secret = "test-secret-that-is-at-least-32-characters-long"
    settings = Settings(app_env="test", auth_required=True, auth_token_secret=secret)
    api = TestClient(create_app(settings, InterviewService()))
    tokens = TokenService(secret)
    recruiter = tokens.issue(Principal("r1", "tenant-a", frozenset({"recruiter"})))
    candidate = tokens.issue(Principal("c1", "tenant-a", frozenset({"candidate"})))
    other_candidate = tokens.issue(Principal("c2", "tenant-a", frozenset({"candidate"})))
    outsider = tokens.issue(Principal("r2", "tenant-b", frozenset({"recruiter"})))
    created = api.post(
        "/interviews",
        headers={"Authorization": f"Bearer {recruiter}"},
        json={
            "job_title": "Role",
            "resume_text": "Resume",
            "job_details": "Details",
            "candidate_id": "c1",
        },
    )
    assert created.status_code == 201
    interview_id = created.json()["interview_id"]
    assert (
        api.get(
            f"/interviews/{interview_id}",
            headers={"Authorization": f"Bearer {outsider}"},
        ).status_code
        == 404
    )
    assert (
        api.post(
            "/interviews",
            headers={"Authorization": f"Bearer {candidate}"},
            json={"job_title": "Role", "resume_text": "Resume", "job_details": "Details"},
        ).status_code
        == 403
    )
    candidate_view = api.get(
        f"/interviews/{interview_id}",
        headers={"Authorization": f"Bearer {candidate}"},
    ).json()
    assert "rubric" not in candidate_view
    assert "scores" not in candidate_view
    assert "intent_events" not in candidate_view
    assert "audit_events" not in candidate_view
    assert "adaptive_candidates" not in candidate_view
    assert "adaptive_questions" not in candidate_view
    assert "question_history" not in candidate_view
    assert "question_statuses" not in candidate_view
    assert (
        api.post(
            f"/interviews/{interview_id}/start",
            headers={"Authorization": f"Bearer {other_candidate}"},
        ).status_code
        == 404
    )
