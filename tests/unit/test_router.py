import pytest

from agentic_interviewer.adapters.fake import FakeUnifiedAdapter
from agentic_interviewer.adapters.router import BudgetLedger, CapabilityRouter, ProviderBundle
from agentic_interviewer.domain.models import (
    ExecutionControls,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderError,
)


def controls(**overrides):
    return ExecutionControls(idempotency_key="test", **overrides)


def test_fake_profile_can_fill_all_logical_ports():
    adapter = FakeUnifiedAdapter()
    router = CapabilityRouter(
        {"fake": ProviderBundle(adapter, adapter, adapter)},
        default_profile="fake",
    )
    bundle = router.bundle()
    assert bundle.reasoning is bundle.transcription is bundle.synthesis


def test_same_router_accepts_composed_specialized_fake_profile():
    unified = FakeUnifiedAdapter()
    specialized = ProviderBundle(
        reasoning=FakeUnifiedAdapter(),
        transcription=FakeUnifiedAdapter(),
        synthesis=FakeUnifiedAdapter(),
    )
    router = CapabilityRouter(
        {
            "unified": ProviderBundle(unified, unified, unified),
            "specialized": specialized,
        },
        default_profile="unified",
    )
    selected = router.bundle("specialized")
    assert selected is specialized
    assert selected.reasoning is specialized.reasoning
    assert selected.transcription is specialized.transcription
    assert selected.synthesis is specialized.synthesis
    assert router.bundle() is not specialized


def test_router_rejects_external_processing_when_disallowed():
    capabilities = ProviderCapabilities(
        provider="hosted", model="example", external_processing=True
    )
    with pytest.raises(ProviderError) as exc:
        CapabilityRouter.assert_eligible(capabilities, controls())
    assert exc.value.code == "policy_denied"


def test_router_rejects_unsupported_verbatim_requirement():
    capabilities = ProviderCapabilities(provider="local", model="example")
    with pytest.raises(ProviderError) as exc:
        CapabilityRouter.assert_eligible(
            capabilities,
            controls(require_verbatim_output=True),
        )
    assert exc.value.code == "unsupported_capability"


@pytest.mark.parametrize(
    ("control_name", "capability_name"),
    [
        ("require_live_audio_input", "live_audio_input"),
        ("require_partial_transcripts", "partial_transcripts"),
        ("require_server_vad", "server_vad"),
        ("require_manual_commit", "manual_commit"),
        ("require_streaming_audio_output", "streaming_audio_output"),
        ("require_barge_in", "barge_in"),
        ("require_word_timestamps", "word_timestamps"),
        ("require_segment_timestamps", "segment_timestamps"),
    ],
)
def test_router_fails_closed_when_a_mandatory_capability_is_missing(
    control_name: str,
    capability_name: str,
):
    capabilities = ProviderCapabilities(provider="local", model="speech")

    with pytest.raises(ProviderError) as exc:
        CapabilityRouter.assert_eligible(capabilities, controls(**{control_name: True}))

    assert exc.value.code == "unsupported_capability"
    assert capability_name.replace("_", " ") in str(exc.value)


@pytest.mark.parametrize(
    ("control_name", "capability_name"),
    [
        ("require_live_audio_input", "live_audio_input"),
        ("require_partial_transcripts", "partial_transcripts"),
        ("require_server_vad", "server_vad"),
        ("require_manual_commit", "manual_commit"),
        ("require_streaming_audio_output", "streaming_audio_output"),
        ("require_barge_in", "barge_in"),
        ("require_word_timestamps", "word_timestamps"),
        ("require_segment_timestamps", "segment_timestamps"),
    ],
)
def test_router_accepts_each_explicitly_advertised_mandatory_capability(
    control_name: str,
    capability_name: str,
):
    capabilities = ProviderCapabilities(
        provider="local",
        model="speech",
        **{capability_name: True},
    )

    CapabilityRouter.assert_eligible(capabilities, controls(**{control_name: True}))


def test_budget_ledger_aggregates_normalized_usage():
    ledger = BudgetLedger()
    state = ledger.record(
        NormalizedUsage(
            provider="fake",
            model="fake",
            capability="reasoning",
            input_tokens=10,
            output_tokens=3,
        )
    )
    assert state.input_tokens == 10
    assert state.output_tokens == 3
