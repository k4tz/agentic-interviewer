from contextlib import asynccontextmanager
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from pydantic import BaseModel, Field

from agentic_interviewer.api.voice_realtime import create_voice_realtime_router
from agentic_interviewer.composition.providers import ProviderRuntime, build_provider_runtime
from agentic_interviewer.config import Settings, get_settings
from agentic_interviewer.domain.models import (
    AnswerAnalysis,
    AnswerDisposition,
    ExecutionControls,
    InterviewPlanningControls,
    NormalizedUsage,
    ProviderError,
    SpeechRequest,
    TranscriptionRequest,
)
from agentic_interviewer.operations.controls import (
    AdmissionController,
    AdmissionRejected,
    BudgetExceeded,
    BudgetLimits,
    CircuitBreaker,
    RateLimiter,
    SessionBudget,
)
from agentic_interviewer.operations.telemetry import (
    Correlation,
    InMemorySpanExporter,
    MetricRegistry,
    Tracer,
)
from agentic_interviewer.persistence import (
    CandidateCatalog,
    CatalogUnavailableError,
    InMemoryCandidateCatalog,
    IntakeRecord,
    JobNotFoundError,
    PostgresCandidateCatalog,
    ResumeRecord,
    SQLiteRepositories,
)
from agentic_interviewer.security import Principal, TokenService
from agentic_interviewer.security.tokens import AuthenticationError
from agentic_interviewer.services.candidate_intake import (
    ResumeValidationError,
    extract_resume_text,
    prepare_question_bank,
)
from agentic_interviewer.services.interviews import (
    InterviewConflictError,
    InterviewNotFoundError,
    InterviewService,
)
from agentic_interviewer.services.turn_analysis import (
    analyze_candidate_answer,
    conservative_answer_analysis,
)


def _planning_failure_code(exc: Exception) -> str:
    """Map planning failures to bounded, PII-free operational codes."""

    if isinstance(exc, AdmissionRejected):
        return "planning_capacity_rejected"
    if isinstance(exc, BudgetExceeded):
        return "planning_budget_exceeded"
    if isinstance(exc, ProviderError) and exc.code == "empty_provider_response":
        return "planning_empty_final_response"
    if isinstance(exc, ProviderError) and exc.code == "invalid_provider_response":
        return "planning_provider_invalid_response"
    if isinstance(exc, InterviewConflictError):
        return "planning_output_rejected"
    return "planning_provider_failed"


class CreateInterviewRequest(BaseModel):
    job_title: str = Field(min_length=1, max_length=200)
    resume_text: str = Field(min_length=1, max_length=100_000)
    job_details: str = Field(min_length=1, max_length=100_000)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=200)
    planning_controls: InterviewPlanningControls = Field(default_factory=InterviewPlanningControls)
    company_questions: list[str] = Field(default_factory=list, max_length=20)


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)


class ClaimCorrectionRequest(BaseModel):
    profile: str
    claim_id: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100_000)
    actor: str = Field(min_length=1, max_length=200)


class TranscriptCorrectionRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=200)
    corrected_text: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(min_length=1, max_length=2_000)


class AssessmentOverrideRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)
    competency_scores: list[dict]


class AppealRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=5_000)


class ExportRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


def create_app(
    settings: Settings | None = None,
    service: InterviewService | None = None,
    providers: ProviderRuntime | None = None,
    catalog: CandidateCatalog | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    repository: SQLiteRepositories | None = None
    if service is None:
        database_path = app_settings.sqlite_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteRepositories(database_path)
        interview_service = InterviewService(repository=repository)
    else:
        interview_service = service
    provider_runtime = providers or build_provider_runtime(app_settings)
    candidate_catalog = catalog or (
        PostgresCandidateCatalog(app_settings.database_url)
        if app_settings.database_url
        else InMemoryCandidateCatalog()
    )
    token_service = (
        TokenService(app_settings.auth_token_secret or "") if app_settings.auth_required else None
    )
    rate_limiter = RateLimiter(
        capacity=app_settings.rate_limit_capacity,
        refill_per_second=app_settings.rate_limit_refill_per_second,
    )
    admission = AdmissionController(
        max_concurrent=app_settings.max_concurrent_model_calls,
        max_per_tenant=app_settings.max_concurrent_model_calls_per_tenant,
    )
    budgets: dict[str, SessionBudget] = {}
    metrics = MetricRegistry(allowed_labels={"capability", "outcome", "provider"})
    span_exporter = InMemorySpanExporter()
    tracer = Tracer(span_exporter)
    circuits = {
        capability: CircuitBreaker(
            failure_threshold=app_settings.provider_circuit_failure_threshold,
            recovery_seconds=app_settings.provider_circuit_recovery_seconds,
        )
        for capability in ("reasoning", "transcription", "synthesis")
    }

    def budget_for(interview_id: str) -> SessionBudget:
        existing = budgets.get(interview_id)
        if existing is not None:
            return existing
        budget = SessionBudget(
            BudgetLimits(
                input_tokens=app_settings.max_input_tokens,
                output_tokens=app_settings.max_output_tokens,
                audio_seconds=app_settings.max_audio_seconds,
                synthesized_characters=app_settings.max_synthesized_characters,
                estimated_cost_usd=Decimal(app_settings.max_estimated_cost_usd),
            )
        )
        state = interview_service.get(interview_id)
        for usage in state["usage"]:
            budget.record(NormalizedUsage.model_validate(usage))
        budgets[interview_id] = budget
        return budget

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await provider_runtime.aclose()
            if repository is not None:
                repository.close()

    app = FastAPI(title=app_settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.admission = admission
    app.state.candidate_catalog = candidate_catalog
    if repository is not None:
        app.state.repository = repository

    def get_service() -> InterviewService:
        return interview_service

    Service = Annotated[InterviewService, Depends(get_service)]

    def get_candidate_catalog() -> CandidateCatalog:
        return candidate_catalog

    Catalog = Annotated[CandidateCatalog, Depends(get_candidate_catalog)]

    def authenticate(
        authorization: str | None = Header(default=None),
        x_tenant_id: str = Header(default="default", alias="X-Tenant-ID"),
        x_actor_id: str = Header(default="local-user", alias="X-Actor-ID"),
    ) -> Principal:
        if token_service is None:
            principal = Principal(
                subject=x_actor_id,
                tenant_id=x_tenant_id,
                roles=frozenset({"candidate", "recruiter", "reviewer"}),
            )
        else:
            if authorization is None or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="bearer token required")
            try:
                principal = token_service.verify(authorization.removeprefix("Bearer "))
            except AuthenticationError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            rate_limiter.acquire(principal.tenant_id)
        except AdmissionRejected as exc:
            metrics.add("interviewer_admission_rejections_total", labels={"outcome": exc.reason})
            raise HTTPException(
                status_code=429,
                detail=exc.reason,
                headers={"Retry-After": str(max(1, int(exc.retry_after_seconds or 1)))},
            ) from exc
        return principal

    PrincipalDep = Annotated[Principal, Depends(authenticate)]

    def authorize_realtime_connection(websocket: WebSocket) -> None:
        principal = authenticate(
            authorization=websocket.headers.get("authorization"),
            x_tenant_id=websocket.headers.get("x-tenant-id", "default"),
            x_actor_id=websocket.headers.get("x-actor-id", "local-user"),
        )
        _require_role(principal, "candidate")

    app.include_router(
        create_voice_realtime_router(
            app_settings,
            provider_runtime.realtime,
            authorize_connection=authorize_realtime_connection,
        )
    )

    async def evaluate_answer_turn(
        interview_id: str, answer_text: str, principal: Principal
    ) -> tuple[dict | None, NormalizedUsage | None]:
        state = interview_service.get_for_tenant(principal.tenant_id, interview_id)
        if app_settings.provider_profile == "fake":
            words = answer_text.split()
            fake_analysis = (
                AnswerAnalysis(disposition=AnswerDisposition.ACCEPT, confidence=1.0)
                if len(words) >= 4
                else conservative_answer_analysis(answer_text)
            )
            return fake_analysis.model_dump(mode="json"), None
        try:
            with admission.lease(principal.tenant_id):
                with circuits["reasoning"].lease():
                    analysis, usage = await analyze_candidate_answer(
                        interview_id=interview_id,
                        state=state,
                        answer=answer_text,
                        reasoning=provider_runtime.router.bundle().reasoning,
                        allow_external_processing=app_settings.allow_external_model_processing,
                        allowed_regions=app_settings.allowed_provider_regions,
                        retain_provider_data=app_settings.allow_provider_data_retention,
                    )
            return (
                analysis.model_dump(mode="json") if analysis is not None else None,
                usage if isinstance(usage, NormalizedUsage) else None,
            )
        except (BudgetExceeded, ProviderError, ValueError):
            fallback = conservative_answer_analysis(answer_text)
            return fallback.model_dump(mode="json"), None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": app_settings.app_env}

    @app.get("/providers/health")
    async def provider_health():
        probes = await provider_runtime.health()
        provider_status = "ok" if all(item["healthy"] for item in probes) else "degraded"
        return {"status": provider_status, "providers": probes}

    @app.get("/api/candidate/jobs")
    def candidate_jobs(catalog: Catalog, principal: PrincipalDep):
        _require_role(principal, "candidate")
        try:
            return {"jobs": [job.public_dict() for job in catalog.list_jobs()]}
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/candidate/intakes", status_code=status.HTTP_201_CREATED)
    async def create_candidate_intake(
        service: Service,
        catalog: Catalog,
        principal: PrincipalDep,
        resume: Annotated[UploadFile, File(description="Candidate resume")],
        job_id: Annotated[str, Form(min_length=1, max_length=200)],
    ):
        _require_role(principal, "candidate")
        try:
            job = catalog.get_job(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        raw_resume = await resume.read(app_settings.max_resume_bytes + 1)
        await resume.close()
        if len(raw_resume) > app_settings.max_resume_bytes:
            raise HTTPException(status_code=413, detail="resume exceeds the configured size limit")
        try:
            filename, resume_text = extract_resume_text(resume.filename or "", raw_resume)
        except ResumeValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        resume_record = ResumeRecord(
            id=str(uuid4()),
            filename=filename,
            content_type=("application/pdf" if filename.lower().endswith(".pdf") else "text/plain"),
            sha256=sha256(raw_resume).hexdigest(),
            raw_bytes=raw_resume,
            extracted_text=resume_text,
        )
        try:
            catalog.store_resume(resume_record)
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        state = _call(
            service.create_buffered,
            job_title=job.title,
            resume_text=resume_text,
            job_details=job.job_details,
            tenant_id=principal.tenant_id,
            candidate_id=principal.subject,
            company_questions=list(job.company_questions),
        )
        planning_mode = "deterministic"
        planning_status = "ready"
        planning_failure_code: str | None = None
        requires_planning_acknowledgement = False
        planning_note = "Prepared with the bounded deterministic question template."
        if app_settings.provider_profile != "fake":
            try:
                correlation = Correlation.from_ids(
                    tenant_id=principal.tenant_id,
                    interview_id=state["interview_id"],
                    turn_id="prepare-question-bank",
                    salt=app_settings.auth_token_secret or "development-only",
                )
                with tracer.span("provider.reasoning", correlation=correlation):
                    with admission.lease(principal.tenant_id):
                        with circuits["reasoning"].lease():
                            state, planning_usage = await prepare_question_bank(
                                interview_id=state["interview_id"],
                                state=state,
                                resume_text=resume_text,
                                job_details=job.job_details,
                                reasoning=provider_runtime.router.bundle().reasoning,
                                service=service,
                                allow_external_processing=(
                                    app_settings.allow_external_model_processing
                                ),
                                allowed_regions=app_settings.allowed_provider_regions,
                                retain_provider_data=app_settings.allow_provider_data_retention,
                            )
                            budget_for(state["interview_id"]).record(planning_usage)
                            state = service.record_usage(
                                state["interview_id"],
                                planning_usage.model_dump(mode="json"),
                            )
                planning_mode = "reasoning"
                planning_note = (
                    "Prepared from the resume and job details by the reasoning provider."
                )
                reasoning_provider = (
                    provider_runtime.router.bundle().reasoning.capabilities.provider
                )
                metrics.add(
                    "interviewer_provider_calls_total",
                    labels={
                        "capability": "reasoning",
                        "outcome": "ok",
                        "provider": reasoning_provider,
                    },
                )
            except AdmissionRejected as exc:
                planning_failure_code = _planning_failure_code(exc)
                metrics.add(
                    "interviewer_planning_failures_total",
                    labels={"outcome": planning_failure_code},
                )
                raise _control_http_error(exc) from exc
            except (BudgetExceeded, ProviderError, InterviewConflictError) as exc:
                planning_failure_code = _planning_failure_code(exc)
                metrics.add(
                    "interviewer_planning_failures_total",
                    labels={"outcome": planning_failure_code},
                )
                if app_settings.app_env == "production":
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "message": (
                                "Interview preparation did not pass the planning quality gate."
                            ),
                            "code": planning_failure_code,
                        },
                    ) from exc
                planning_mode = "deterministic_fallback"
                planning_status = "degraded"
                requires_planning_acknowledgement = True
                planning_note = (
                    "Development safeguard: model-backed planning did not pass its quality gate "
                    f"({planning_failure_code}). A fallback plan is loaded; explicitly "
                    "acknowledge degraded mode before beginning."
                )
                metrics.add(
                    "interviewer_provider_calls_total",
                    labels={
                        "capability": "reasoning",
                        "outcome": "fallback",
                        "provider": (
                            provider_runtime.router.bundle().reasoning.capabilities.provider
                        ),
                    },
                )

        state = _call(
            service.approve_setup,
            state["interview_id"],
            "system:intake",
            f"Candidate intake prepared using {planning_mode} planning.",
        )
        intake = IntakeRecord(
            id=str(uuid4()),
            job_id=job.id,
            resume_id=resume_record.id,
            interview_id=state["interview_id"],
            candidate_id=principal.subject,
            status=state["status"],
        )
        try:
            catalog.create_intake(intake)
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "intake_id": intake.id,
            "interview_id": state["interview_id"],
            "status": state["status"],
            "job": job.public_dict(),
            "resume": {"id": resume_record.id, "filename": filename},
            "question_count": len(state["plan"]["questions"]),
            "planning_mode": planning_mode,
            "planning_status": planning_status,
            "planning_failure_code": planning_failure_code,
            "requires_planning_acknowledgement": requires_planning_acknowledgement,
            "planning_note": planning_note,
        }

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(content=metrics.render_prometheus(), media_type="text/plain")

    @app.post("/interviews", status_code=status.HTTP_201_CREATED)
    def create_interview(
        request: CreateInterviewRequest, service: Service, principal: PrincipalDep
    ):
        _require_role(principal, "recruiter")
        if request.planning_controls.max_total_questions > app_settings.max_questions:
            raise HTTPException(
                status_code=422,
                detail=("planning_controls.max_total_questions exceeds the organization limit"),
            )
        return _call(
            service.create_buffered,
            tenant_id=principal.tenant_id,
            **request.model_dump(),
        )

    @app.get("/interviews/{interview_id}")
    def get_interview(interview_id: str, service: Service, principal: PrincipalDep):
        state = _call(service.get_for_tenant, principal.tenant_id, interview_id)
        _require_candidate_binding(principal, state)
        return _view_for(principal, state)

    @app.post("/interviews/{interview_id}/start")
    def start_interview(interview_id: str, service: Service, principal: PrincipalDep):
        _authorize(service, principal, interview_id, "candidate")
        return _view_for(principal, _call(service.start, interview_id))

    @app.patch("/interviews/{interview_id}/source-claims")
    def correct_source_claim(
        interview_id: str,
        request: ClaimCorrectionRequest,
        service: Service,
        principal: PrincipalDep,
    ):
        _authorize(service, principal, interview_id, "recruiter")
        return _call(
            service.correct_claim,
            interview_id,
            request.profile,
            request.claim_id,
            request.value,
            principal.subject,
        )

    @app.post("/interviews/{interview_id}/setup-approval")
    def approve_setup(
        interview_id: str, request: ReviewRequest, service: Service, principal: PrincipalDep
    ):
        _authorize(service, principal, interview_id, "recruiter")
        return _call(service.approve_setup, interview_id, principal.subject, request.reason)

    @app.post("/interviews/{interview_id}/answers")
    async def answer(
        interview_id: str, request: AnswerRequest, service: Service, principal: PrincipalDep
    ):
        _authorize(service, principal, interview_id, "candidate")
        try:
            analysis, usage = await evaluate_answer_turn(interview_id, request.text, principal)
        except AdmissionRejected as exc:
            raise _control_http_error(exc) from exc
        state = _call(service.answer, interview_id, request.text, analysis)
        if usage is not None:
            budget_for(interview_id).record(usage)
            state = service.record_usage(interview_id, usage.model_dump(mode="json"))
        return _view_for(principal, state)

    @app.post("/interviews/{interview_id}/audio-answers")
    async def audio_answer(
        interview_id: str,
        service: Service,
        principal: PrincipalDep,
        audio: bytes = Body(media_type="application/octet-stream"),
        audio_format: str = Query(default="wav"),
        language: str = Query(default="en"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        _authorize(service, principal, interview_id, "candidate")
        controls = ExecutionControls(
            idempotency_key=idempotency_key,
            max_audio_seconds=app_settings.max_audio_seconds,
            allow_external_processing=app_settings.allow_external_model_processing,
            allowed_regions=app_settings.allowed_provider_regions,
            retain_provider_data=app_settings.allow_provider_data_retention,
        )
        try:
            correlation = Correlation.from_ids(
                tenant_id=principal.tenant_id,
                interview_id=interview_id,
                turn_id=idempotency_key,
                salt=app_settings.auth_token_secret or "development-only",
            )
            with tracer.span("provider.transcription", correlation=correlation):
                with admission.lease(principal.tenant_id):
                    with circuits["transcription"].lease():
                        result = await provider_runtime.router.bundle().transcription.transcribe(
                            TranscriptionRequest(
                                audio=audio,
                                audio_format=audio_format,
                                language=language,
                                controls=controls,
                            )
                        )
            budget_for(interview_id).record(result.usage)
            metrics.add(
                "interviewer_provider_calls_total",
                labels={
                    "capability": "transcription",
                    "outcome": "ok",
                    "provider": result.usage.provider,
                },
            )
            analysis, reasoning_usage = await evaluate_answer_turn(
                interview_id, result.transcript.text, principal
            )
            state = _call(service.answer, interview_id, result.transcript.text, analysis)
            state = service.record_usage(interview_id, result.usage.model_dump(mode="json"))
            if reasoning_usage is not None:
                budget_for(interview_id).record(reasoning_usage)
                state = service.record_usage(interview_id, reasoning_usage.model_dump(mode="json"))
            return _view_for(principal, state)
        except (AdmissionRejected, BudgetExceeded) as exc:
            raise _control_http_error(exc) from exc
        except ProviderError as exc:
            raise _provider_http_error(exc) from exc

    @app.post("/interviews/{interview_id}/speech")
    async def synthesize_question(
        interview_id: str,
        service: Service,
        principal: PrincipalDep,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        output_format: str = Query(default="wav"),
        cue: Literal["silence_nudge"] | None = Query(default=None),
    ):
        _authorize(service, principal, interview_id, "candidate")
        state = _call(service.get, interview_id)
        if not state["last_response"] and cue is None:
            raise HTTPException(status_code=409, detail="there is no question to synthesize")
        speech_text = (
            "Take your time. You can begin when you're ready, or say you'd like to move on."
            if cue == "silence_nudge"
            else state["last_response"]
        )
        controls = ExecutionControls(
            idempotency_key=idempotency_key,
            max_output_characters=app_settings.max_synthesized_characters,
            allow_external_processing=app_settings.allow_external_model_processing,
            allowed_regions=app_settings.allowed_provider_regions,
            retain_provider_data=app_settings.allow_provider_data_retention,
        )
        chunks: list[bytes] = []
        try:
            correlation = Correlation.from_ids(
                tenant_id=principal.tenant_id,
                interview_id=interview_id,
                turn_id=idempotency_key,
                salt=app_settings.auth_token_secret or "development-only",
            )
            with tracer.span("provider.synthesis", correlation=correlation):
                with admission.lease(principal.tenant_id):
                    with circuits["synthesis"].lease():
                        async for event in provider_runtime.router.bundle().synthesis.synthesize(
                            SpeechRequest(
                                text=speech_text,
                                output_format=output_format,
                                controls=controls,
                            )
                        ):
                            chunks.append(event.audio)
                            if event.usage:
                                budget_for(interview_id).record(event.usage)
                                metrics.add(
                                    "interviewer_provider_calls_total",
                                    labels={
                                        "capability": "synthesis",
                                        "outcome": "ok",
                                        "provider": event.usage.provider,
                                    },
                                )
                                service.record_usage(
                                    interview_id, event.usage.model_dump(mode="json")
                                )
        except (AdmissionRejected, BudgetExceeded) as exc:
            raise _control_http_error(exc) from exc
        except ProviderError as exc:
            raise _provider_http_error(exc) from exc
        media_type = {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "flac": "audio/flac",
            "pcm": "application/octet-stream",
        }.get(output_format, "application/octet-stream")
        return Response(content=b"".join(chunks), media_type=media_type)

    @app.post("/interviews/{interview_id}/review")
    def review(
        interview_id: str, request: ReviewRequest, service: Service, principal: PrincipalDep
    ):
        _authorize(service, principal, interview_id, "reviewer")
        return _call(service.approve, interview_id, principal.subject, request.reason)

    @app.patch("/interviews/{interview_id}/transcript")
    def correct_transcript(
        interview_id: str,
        request: TranscriptCorrectionRequest,
        service: Service,
        principal: PrincipalDep,
    ):
        _authorize(service, principal, interview_id, "candidate")
        state = _call(
            service.correct_transcript,
            interview_id,
            request.turn_id,
            request.corrected_text,
            request.reason,
        )
        return _view_for(principal, state)

    @app.post("/interviews/{interview_id}/review/override")
    def override_assessment(
        interview_id: str,
        request: AssessmentOverrideRequest,
        service: Service,
        principal: PrincipalDep,
    ):
        _authorize(service, principal, interview_id, "reviewer")
        return _call(
            service.override_assessment,
            interview_id,
            principal.subject,
            request.reason,
            request.competency_scores,
        )

    @app.post("/interviews/{interview_id}/appeals", status_code=status.HTTP_202_ACCEPTED)
    def appeal(
        interview_id: str, request: AppealRequest, service: Service, principal: PrincipalDep
    ):
        _authorize(service, principal, interview_id, "candidate")
        return _view_for(principal, _call(service.appeal, interview_id, request.reason))

    @app.post("/interviews/{interview_id}/exports")
    def export(
        interview_id: str, request: ExportRequest, service: Service, principal: PrincipalDep
    ):
        _authorize(service, principal, interview_id, "reviewer")
        return _call(service.export, interview_id, request.idempotency_key)

    return app


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="interview not found") from exc
    except InterviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _provider_http_error(error: ProviderError) -> HTTPException:
    status_code = {
        "invalid_request": 400,
        "budget_exceeded": 422,
        "policy_denied": 403,
        "unsupported_capability": 422,
        "unsupported_control": 422,
        "rate_limited": 429,
        "provider_timeout": 504,
        "provider_unavailable": 503,
    }.get(error.code, 502)
    detail = {"code": error.code, "message": str(error)}
    return HTTPException(status_code=status_code, detail=detail)


def _require_role(principal: Principal, role: str) -> None:
    if role not in principal.roles:
        raise HTTPException(status_code=403, detail=f"{role} role required")


def _authorize(
    service: InterviewService, principal: Principal, interview_id: str, role: str
) -> None:
    _require_role(principal, role)
    state = _call(service.get_for_tenant, principal.tenant_id, interview_id)
    if role == "candidate":
        _require_candidate_binding(principal, state)


def _require_candidate_binding(principal: Principal, state: dict) -> None:
    is_candidate_only = not principal.roles.intersection({"recruiter", "reviewer"})
    if is_candidate_only and state["candidate_id"] != principal.subject:
        raise HTTPException(status_code=404, detail="interview not found")


def _control_http_error(error: AdmissionRejected | BudgetExceeded) -> HTTPException:
    if isinstance(error, AdmissionRejected):
        return HTTPException(status_code=429, detail=error.reason)
    return HTTPException(
        status_code=422,
        detail={"code": "session_budget_exceeded", "dimensions": error.dimensions},
    )


def _view_for(principal: Principal, state: dict) -> dict:
    if principal.roles.intersection({"recruiter", "reviewer"}):
        return state
    allowed = {
        "interview_id",
        "candidate_id",
        "status",
        "current_question_index",
        "last_response",
        "answers",
        "appeals",
    }
    return {key: value for key, value in state.items() if key in allowed}


app = create_app()
