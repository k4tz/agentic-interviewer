from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentic_interviewer.adapters.speaches import SpeachesConfig

_SPEACHES_DEFAULTS = SpeachesConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Agentic Interviewer"
    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    auth_required: bool = False
    auth_token_secret: str | None = None
    sqlite_path: str = ".data/interviewer.db"
    database_url: str | None = None
    max_resume_bytes: int = Field(default=5_242_880, ge=1_024, le=20_971_520)
    provider_profile: str = "fake"
    reasoning_provider_name: str = "openai-compatible"
    reasoning_base_url: str = ""
    reasoning_api_key: str | None = Field(default=None, repr=False)
    reasoning_region: str = "vendor"
    reasoning_external_processing: bool = True
    reasoning_retains_provider_data: bool = True
    reasoning_model: str = ""
    reasoning_enable_thinking: bool | None = None
    reasoning_check_health_endpoint: bool = False
    reasoning_max_completion_tokens: int = Field(default=1_024, ge=64, le=8_192)
    reasoning_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    reasoning_top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    reasoning_presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    speaches_base_url: str = _SPEACHES_DEFAULTS.base_url
    speaches_api_key: str | None = Field(default=None, repr=False)
    speaches_stt_model: str = _SPEACHES_DEFAULTS.transcription_model
    speaches_tts_model: str = _SPEACHES_DEFAULTS.synthesis_model
    speaches_default_voice: str | None = _SPEACHES_DEFAULTS.default_voice
    speaches_region: str = _SPEACHES_DEFAULTS.region
    speaches_external_processing: bool = _SPEACHES_DEFAULTS.external_processing
    speaches_retains_provider_data: bool = _SPEACHES_DEFAULTS.retains_provider_data
    speaches_check_health_endpoint: bool = _SPEACHES_DEFAULTS.check_health_endpoint
    audio_provider_name: str = "openai-compatible"
    audio_base_url: str = ""
    audio_api_key: str | None = Field(default=None, repr=False)
    audio_stt_model: str = ""
    audio_tts_model: str = ""
    audio_default_voice: str | None = None
    audio_region: str = "vendor"
    audio_external_processing: bool = True
    audio_retains_provider_data: bool = True
    audio_check_health_endpoint: bool = False
    allow_external_model_processing: bool = False
    allow_provider_data_retention: bool = False
    allowed_provider_regions: set[str] = Field(default_factory=set)
    max_questions: int = 20
    max_redirects_per_question: int = 2
    max_input_tokens: int = 32_000
    max_output_tokens: int = 6_000
    max_audio_seconds: float = 300.0
    max_synthesized_characters: int = 12_000
    max_estimated_cost_usd: Decimal = Decimal("1.00")
    rate_limit_capacity: int = 120
    rate_limit_refill_per_second: float = 2.0
    max_concurrent_model_calls: int = 8
    max_concurrent_model_calls_per_tenant: int = 8
    provider_circuit_failure_threshold: int = 3
    provider_circuit_recovery_seconds: float = 30.0
    candidate_ui_enabled: bool = True
    realtime_max_message_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    realtime_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    realtime_vad_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    realtime_silence_duration_ms: int = Field(default=1_500, ge=250, le=10_000)

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.app_env == "production" and not self.auth_required:
            raise ValueError("AUTH_REQUIRED must be true in production")
        if self.auth_required and (
            self.auth_token_secret is None or len(self.auth_token_secret) < 32
        ):
            raise ValueError("AUTH_TOKEN_SECRET must contain at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
