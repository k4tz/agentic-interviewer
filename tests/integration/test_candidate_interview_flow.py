import importlib

from fastapi.testclient import TestClient

from agentic_interviewer.api.app import create_app
from agentic_interviewer.composition.providers import build_provider_runtime
from agentic_interviewer.config import Settings
from agentic_interviewer.persistence import InMemoryCandidateCatalog
from agentic_interviewer.security import Principal, TokenService
from agentic_interviewer.services import InterviewService


def _client(**settings) -> TestClient:
    return TestClient(
        create_app(
            Settings(app_env="test", **settings),
            InterviewService(),
            catalog=InMemoryCandidateCatalog(),
            providers=build_provider_runtime(Settings(_env_file=None, provider_profile="fake")),
        )
    )


def test_candidate_can_upload_resume_prepare_and_start_interview():
    api = _client()
    jobs = api.get("/api/candidate/jobs")

    assert jobs.status_code == 200
    selected = jobs.json()["jobs"][0]
    resume = (
        "Taylor Candidate\n"
        "Platform engineer with seven years of Python experience. "
        "Led an API migration that reduced latency by 35 percent and operated PostgreSQL."
    )
    prepared = api.post(
        "/api/candidate/intakes",
        data={"job_id": selected["id"]},
        files={"resume": ("resume.txt", resume, "text/plain")},
    )

    assert prepared.status_code == 201
    body = prepared.json()
    assert body["status"] == "ready"
    assert body["question_count"] == 11
    assert body["planning_mode"] == "deterministic"
    assert body["planning_status"] == "ready"
    assert body["planning_failure_code"] is None
    assert body["requires_planning_acknowledgement"] is False
    assert body["resume"]["filename"] == "resume.txt"

    started = api.post(f"/interviews/{body['interview_id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"
    assert started.json()["last_response"]

    speech = api.post(
        f"/interviews/{body['interview_id']}/speech",
        params={"output_format": "wav"},
        headers={"Idempotency-Key": "candidate-speech-0001"},
    )
    assert speech.status_code == 200
    assert speech.content == started.json()["last_response"].encode()

    nudge = api.post(
        f"/interviews/{body['interview_id']}/speech",
        params={"output_format": "wav", "cue": "silence_nudge"},
        headers={"Idempotency-Key": "candidate-nudge-0001"},
    )
    assert nudge.status_code == 200
    assert b"Take your time" in nudge.content


def test_candidate_intake_rejects_unknown_jobs_and_unsupported_resume_files():
    api = _client()
    resume = "Candidate resume text with enough detail to pass the minimum readable length."

    missing_job = api.post(
        "/api/candidate/intakes",
        data={"job_id": "missing"},
        files={"resume": ("resume.txt", resume, "text/plain")},
    )
    assert missing_job.status_code == 404

    invalid_resume = api.post(
        "/api/candidate/intakes",
        data={"job_id": "job-platform-engineer"},
        files={"resume": ("resume.exe", resume, "application/octet-stream")},
    )
    assert invalid_resume.status_code == 422
    assert "PDF, TXT, or Markdown" in invalid_resume.json()["detail"]


def test_candidate_intake_enforces_resume_size_limit_before_processing():
    api = _client(max_resume_bytes=1024)
    response = api.post(
        "/api/candidate/intakes",
        data={"job_id": "job-platform-engineer"},
        files={"resume": ("resume.txt", "x" * 1025, "text/plain")},
    )

    assert response.status_code == 413


def test_candidate_intake_exposes_normalized_degraded_planning_without_pii(monkeypatch):
    app_module = importlib.import_module("agentic_interviewer.api.app")

    async def reject_plan(**_kwargs):
        raise app_module.ProviderError(
            "empty_provider_response", "raw provider output contained PRIVATE-CV-TEXT"
        )

    monkeypatch.setattr(app_module, "prepare_question_bank", reject_plan)
    api = _client(provider_profile="local-specialized")
    selected = api.get("/api/candidate/jobs").json()["jobs"][0]
    prepared = api.post(
        "/api/candidate/intakes",
        data={"job_id": selected["id"]},
        files={
            "resume": (
                "resume.txt",
                "PRIVATE-CV-TEXT candidate experience long enough for resume validation.",
                "text/plain",
            )
        },
    )

    assert prepared.status_code == 201
    body = prepared.json()
    assert body["planning_mode"] == "deterministic_fallback"
    assert body["planning_status"] == "degraded"
    assert body["planning_failure_code"] == "planning_empty_final_response"
    assert body["requires_planning_acknowledgement"] is True
    assert "PRIVATE-CV-TEXT" not in body["planning_note"]

    metrics = api.get("/metrics").text
    assert (
        'interviewer_planning_failures_total{outcome="planning_empty_final_response"} 1' in metrics
    )
    assert "PRIVATE-CV-TEXT" not in metrics


def test_production_candidate_intake_fails_closed_when_planning_fails(monkeypatch):
    app_module = importlib.import_module("agentic_interviewer.api.app")

    async def reject_plan(**_kwargs):
        raise app_module.ProviderError("provider_timeout", "PRIVATE-CV-TEXT")

    monkeypatch.setattr(app_module, "prepare_question_bank", reject_plan)
    secret = "production-test-secret-that-is-long-enough"
    api = TestClient(
        create_app(
            Settings(
                app_env="production",
                auth_required=True,
                auth_token_secret=secret,
                provider_profile="local-specialized",
            ),
            InterviewService(),
            catalog=InMemoryCandidateCatalog(),
            providers=build_provider_runtime(Settings(_env_file=None, provider_profile="fake")),
        )
    )
    token = TokenService(secret).issue(
        Principal("candidate-1", "tenant-1", frozenset({"candidate"}))
    )
    headers = {"Authorization": f"Bearer {token}"}
    selected = api.get("/api/candidate/jobs", headers=headers).json()["jobs"][0]
    prepared = api.post(
        "/api/candidate/intakes",
        headers=headers,
        data={"job_id": selected["id"]},
        files={
            "resume": (
                "resume.txt",
                "Candidate built Python services and reduced API latency by 35 percent.",
                "text/plain",
            )
        },
    )

    assert prepared.status_code == 503
    assert prepared.json()["detail"] == {
        "message": "Interview preparation did not pass the planning quality gate.",
        "code": "planning_provider_failed",
    }
    assert "PRIVATE-CV-TEXT" not in prepared.text
