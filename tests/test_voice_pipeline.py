import base64
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import wave

import httpx

from hexevoice.api.models import AssistantTurnResponse
from hexevoice.assistant import AssistantTurnService
from hexevoice.assistant import LocalIntentFinder
from hexevoice.assistant import VoiceIntentRegistry
from hexevoice.assistant import VoiceIntentStateStore
from hexevoice.config.settings import Settings
from hexevoice.domain_events import AsyncDomainEventPublisher, DomainEventPublishDecision
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.timer_announcements import TimerOwnershipCache
from hexevoice.voice import (
    DeterministicSpeechToTextAdapter,
    DeterministicTextToSpeechAdapter,
    ExternalFasterWhisperSpeechToTextAdapter,
    FasterWhisperSpeechToTextAdapter,
    OpenAiSpeechToTextAdapter,
    OpenAiTextToSpeechAdapter,
    PiperTextToSpeechAdapter,
    ProfiledSpeechToTextAdapter,
    SilenceTrimmingSpeechToTextAdapter,
    SpeechTranscript,
    VoiceTurnAudioSummary,
    VoiceTurnPipeline,
    SttSilenceTrimConfig,
    SttModelProfile,
    build_voice_turn_pipeline,
    resolve_stt_model_profile,
    should_use_stt_fallback,
    trim_stt_silence,
)


class FakeTimerEventPublisher:
    def __init__(self) -> None:
        self.calls = []

    def publish_timer_create(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(status="published", reason="published", topic="hexe/nodes/node-1/events/timer/create_requested")

    def publish_timer_status_request(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(status="published", reason="published", topic="hexe/nodes/node-1/events/timer/status_requested")

    def publish_timer_control_request(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(
            status="published",
            reason="published",
            topic=f"hexe/nodes/node-1/events/timer/{payload['action']}_requested",
        )

    def publish_timer_adjust_request(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(
            status="published",
            reason="published",
            topic="hexe/nodes/node-1/events/timer/adjust_time_requested",
        )

    def publish_timer_snooze_request(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(
            status="published",
            reason="published",
            topic="hexe/nodes/node-1/events/timer/snooze_requested",
        )

    def status(self):
        return {"provider": "fake", "enabled": True, "last_decision": None}


class FakeEndpointCommandDispatcher:
    def __init__(self) -> None:
        self.calls = []

    def dispatch_endpoint_command(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(
            status="queued",
            reason="endpoint_command_queued",
            event_id="endpoint-command-1",
            event_type=payload["command"],
        )


class SlowSpeakerIdClient:
    def __init__(self, *, delay_s: float = 0.5, response: dict | None = None) -> None:
        self.delay_s = delay_s
        self.response = response or {"schema_version": 1, "status": "unknown", "reason": "no_profiles", "match": None}
        self.calls = []

    def identify(self, payload):
        self.calls.append(payload)
        time.sleep(self.delay_s)
        return self.response


class AudioFingerprintSpeakerIdClient:
    def __init__(self) -> None:
        self.calls = []

    def identify(self, payload):
        self.calls.append(payload)
        wav_bytes = base64.b64decode(payload["audio"]["audio_base64"])
        speaker_public_id = "speaker_alex" if b"\x02\x00" * 32 in wav_bytes else "speaker_dan"
        display_name = "Alex" if speaker_public_id == "speaker_alex" else "Dan"
        return {
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": speaker_public_id,
                "display_name": display_name,
                "confidence": 0.94,
                "score": 0.94,
                "score_margin": 0.22,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
            },
        }


class SpyAssistantService:
    def __init__(self, *, spoken_text: str = "handled") -> None:
        self.spoken_text = spoken_text
        self.requests = []

    def handle_turn(self, payload):
        self.requests.append(payload)
        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=payload.session_id,
            heard_text=payload.text,
            reply_text=self.spoken_text,
            spoken_text=self.spoken_text,
            handled_locally=False,
            command=None,
            device_state="speaking",
            provider_id="spy",
            provider_metadata={"speaker_identity_policy": payload.speaker_identity_policy},
        )

    def status(self):
        return {"provider": "spy", "healthy": True, "configured": True}


class CommandAssistantService(SpyAssistantService):
    def __init__(self, *, command: str, metadata: dict | None = None, spoken_text: str = "handled") -> None:
        super().__init__(spoken_text=spoken_text)
        self.command = command
        self.metadata = metadata or {}

    def handle_turn(self, payload):
        self.requests.append(payload)
        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=payload.session_id,
            heard_text=payload.text,
            reply_text=self.spoken_text,
            spoken_text=self.spoken_text,
            handled_locally=True,
            command=self.command,
            device_state="speaking",
            provider_id="spy",
            provider_metadata=self.metadata,
        )


def speaker_id_response(
    *,
    confidence: float = 0.91,
    score_margin: float = 0.2,
    learning_consent: bool = True,
    status: str = "identified",
) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "reason": None,
        "match": {
            "speaker_public_id": "speaker_dan",
            "display_name": "Dan",
            "confidence": confidence,
            "score": confidence,
            "score_margin": score_margin,
            "provider": "deterministic_signal",
            "model_id": "deterministic-signal-v1",
            "learning_consent": learning_consent,
        },
    }


def learning_policy_turn(
    tmp_path,
    *,
    speaker_response: dict,
    audio_bytes: bytes | None = None,
    ambient_audio_bytes: bytes | None = None,
    assistant=None,
):
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant or SpyAssistantService(spoken_text="Opening your calendar."),
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="what's on my calendar"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=SlowSpeakerIdClient(delay_s=0.0, response=speaker_response),
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
    )
    return pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-learning",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=audio_bytes if audio_bytes is not None else (4000).to_bytes(2, byteorder="little", signed=True) * 16000,
            ambient_audio_bytes=ambient_audio_bytes,
        )
    )


class SlowRecognitionPublisher:
    def __init__(self, delay_s: float = 0.3) -> None:
        self.delay_s = delay_s
        self.published = threading.Event()
        self.calls = []

    def publish_timer_create(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "timer", **payload})
        self.published.set()
        return DomainEventPublishDecision(status="published", reason="published", event_type="timer.create_requested")

    def publish_timer_status_request(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "timer_status", **payload})
        self.published.set()
        return DomainEventPublishDecision(status="published", reason="published", event_type="timer.status_requested")

    def publish_timer_control_request(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "timer_control", **payload})
        self.published.set()
        return DomainEventPublishDecision(
            status="published",
            reason="published",
            event_type=f"timer.{payload['action']}_requested",
        )

    def publish_timer_adjust_request(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "timer_adjust", **payload})
        self.published.set()
        return DomainEventPublishDecision(status="published", reason="published", event_type="timer.adjust_time_requested")

    def publish_timer_snooze_request(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "timer_snooze", **payload})
        self.published.set()
        return DomainEventPublishDecision(status="published", reason="published", event_type="timer.snooze_requested")

    def publish_voice_intent_recognized(self, **payload):
        time.sleep(self.delay_s)
        self.calls.append({"type": "recognition", **payload})
        self.published.set()
        return DomainEventPublishDecision(
            status="published",
            reason="published",
            event_id=payload["event_id"],
            event_type="voice.intent.recognized",
        )

    def status(self):
        return {"provider": "slow", "enabled": True, "last_decision": None}


class CaptureTextToSpeechAdapter:
    def __init__(self) -> None:
        self.calls = []

    def synthesize(self, *, endpoint_id, session_id, text, voice=None, audio_format=None, stream_id=None):
        self.calls.append(
            {
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "text": text,
                "voice": voice,
                "audio_format": audio_format,
                "stream_id": stream_id,
            }
        )
        return DeterministicTextToSpeechAdapter().synthesize(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=text,
            voice=voice,
            audio_format=audio_format,
            stream_id=stream_id,
        )

    def status(self):
        return {"provider": "capture", "healthy": True, "configured": True}


def test_voice_turn_pipeline_runs_stt_assistant_and_tts(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=2)
    )

    assert result.transcript.text == "status"
    assert result.assistant_response.command is None
    assert result.assistant_response.spoken_text == "I heard status"
    assert result.tts.content_type == "audio/wav"
    assert result.tts.stream_id.startswith("tts-")
    assert result.timings.stt_ms >= 0
    assert result.timings.assistant_ms >= 0
    assert result.timings.tts_ms >= 0
    assert result.timings.total_ms >= 0


def test_voice_turn_pipeline_attaches_audio_quality_without_blocking(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr("hexevoice.voice.pipeline.record_voice_event", lambda event_type, **fields: events.append({"event_type": event_type, **fields}))
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-low",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=(300).to_bytes(2, byteorder="little", signed=True) * 16000,
        )
    )

    assert result.assistant_response.spoken_text == "I heard status"
    assert result.audio_quality is not None
    assert result.audio_quality.status == "low_level"
    stt_event = next(event for event in events if event["event_type"] == "stt.completed")
    assert stt_event["audio_quality"]["status"] == "low_level"
    assert "audio_bytes" not in stt_event["audio_quality"]


def test_voice_turn_pipeline_attaches_ambient_snr_metadata(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr("hexevoice.voice.pipeline.record_voice_event", lambda event_type, **fields: events.append({"event_type": event_type, **fields}))
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-snr",
            chunk_count=4,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=(4000).to_bytes(2, byteorder="little", signed=True) * 16000,
            ambient_audio_bytes=(100).to_bytes(2, byteorder="little", signed=True) * 16000,
        )
    )

    assert result.audio_quality is not None
    assert result.audio_quality.snr_status == "ok"
    assert result.audio_quality.snr_db is not None and result.audio_quality.snr_db >= 15
    assert result.audio_quality.ambient_duration_ms == 1000
    assert result.audio_quality.speech_duration_ms == 1000
    stt_event = next(event for event in events if event["event_type"] == "stt.completed")
    assert stt_event["audio_quality"]["snr_status"] == "ok"
    assert "ambient_audio_bytes" not in stt_event["audio_quality"]


def test_voice_turn_pipeline_does_not_wait_for_speaker_id_when_not_required(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    speaker_id = SlowSpeakerIdClient(delay_s=0.4)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="hello"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
    )

    started_at = time.perf_counter()
    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.3
    assert result.assistant_response.spoken_text == "I heard hello"
    assert result.speaker_identity is not None
    assert result.speaker_identity.status == "pending"
    assert result.speaker_identity.policy == "use_if_ready"
    assert speaker_id.calls


def test_voice_turn_pipeline_blocks_personal_route_when_speaker_unknown(tmp_path):
    assistant = SpyAssistantService()
    speaker_id = SlowSpeakerIdClient(delay_s=0.0)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="what's on my calendar"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert assistant.requests == []
    assert result.assistant_response.command == "speaker.identity.required"
    assert result.assistant_response.spoken_text == "Who is this?"
    assert result.speaker_identity is not None
    assert result.speaker_identity.status == "unknown"
    assert result.speaker_identity.policy == "required"


def test_voice_turn_pipeline_blocks_registered_required_intent_before_local_handling(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    registry.register_intent(
        intent_id="calendar.today",
        intent_name="Calendar today",
        service_id="calendar.node",
        privacy_class="personal",
        definition={
            "utterance_examples": ["calendar today"],
            "dispatch": {"type": "local_response", "command": "calendar.today"},
            "response": {"reply_text": "Calendar accepted."},
            "matcher": {"type": "exact_example"},
        },
    )
    assistant = AssistantTurnService(
        settings=Settings(node_name="lab-voice"),
        runtime_service=runtime,
        intent_finder=LocalIntentFinder(registry=registry),
    )
    speaker_id = SlowSpeakerIdClient(delay_s=0.0)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="calendar today"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert result.assistant_response.command == "speaker.identity.required"
    assert result.assistant_response.provider_id == "speaker_id_policy"
    assert result.speaker_identity is not None
    assert result.speaker_identity.policy == "required"
    assert assistant.status()["last_intent_latency"] is None


def test_voice_turn_pipeline_passes_identified_speaker_to_required_route(tmp_path):
    assistant = SpyAssistantService(spoken_text="Opening your calendar.")
    speaker_id = SlowSpeakerIdClient(
        delay_s=0.0,
        response={
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": "speaker_dan",
                "display_name": "Dan",
                "confidence": 0.91,
                "score": 0.91,
                "score_margin": 0.2,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
            },
        },
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="what's on my calendar"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=2,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert result.assistant_response.spoken_text == "Opening your calendar."
    assert result.speaker_identity is not None
    assert result.speaker_identity.speaker_public_id == "speaker_dan"
    assert assistant.requests[0].speaker_identity["speaker_public_id"] == "speaker_dan"
    assert assistant.requests[0].speaker_identity_policy == "required"


def test_voice_turn_pipeline_marks_high_confidence_turn_learning_eligible_for_review(tmp_path):
    result = learning_policy_turn(tmp_path, speaker_response=speaker_id_response())

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is True
    decision = result.speaker_identity.learning_eligibility
    assert decision["reason"] == "eligible_for_operator_review"
    assert decision["automatic_learning_enabled"] is False
    assert decision["requires_operator_review"] is True


def test_voice_turn_pipeline_marks_medium_confidence_personalization_only_for_learning(tmp_path):
    result = learning_policy_turn(tmp_path, speaker_response=speaker_id_response(confidence=0.72))

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "confidence_below_high"


def test_voice_turn_pipeline_rejects_low_margin_learning_candidate(tmp_path):
    result = learning_policy_turn(tmp_path, speaker_response=speaker_id_response(score_margin=0.01))

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "score_margin_too_low"


def test_voice_turn_pipeline_rejects_low_snr_learning_candidate(tmp_path):
    loud = (4000).to_bytes(2, byteorder="little", signed=True) * 16000

    result = learning_policy_turn(
        tmp_path,
        speaker_response=speaker_id_response(),
        audio_bytes=loud,
        ambient_audio_bytes=loud,
    )

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "audio_quality_low_snr"


def test_voice_turn_pipeline_rejects_clipped_learning_candidate(tmp_path):
    result = learning_policy_turn(
        tmp_path,
        speaker_response=speaker_id_response(),
        audio_bytes=(32767).to_bytes(2, byteorder="little", signed=True) * 16000,
    )

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "audio_quality_clipped"


def test_voice_turn_pipeline_rejects_short_learning_candidate(tmp_path):
    result = learning_policy_turn(
        tmp_path,
        speaker_response=speaker_id_response(),
        audio_bytes=(4000).to_bytes(2, byteorder="little", signed=True) * 100,
    )

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "audio_quality_short_audio"


def test_voice_turn_pipeline_rejects_forbidden_policy_learning_candidate(tmp_path):
    result = learning_policy_turn(
        tmp_path,
        speaker_response=speaker_id_response(),
        assistant=CommandAssistantService(command="anonymous.local", metadata={"speaker_identity_policy": "forbidden"}),
    )

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "route_forbids_learning"


def test_voice_turn_pipeline_rejects_missing_consent_learning_candidate(tmp_path):
    result = learning_policy_turn(tmp_path, speaker_response=speaker_id_response(learning_consent=False))

    assert result.speaker_identity is not None
    assert result.speaker_identity.learning_eligible is False
    assert result.speaker_identity.learning_eligibility_reason == "missing_learning_consent"


def test_voice_turn_pipeline_blocks_child_safe_endpoint_restricted_content(tmp_path):
    assistant = SpyAssistantService(spoken_text="should not run")
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="tell me a sexual story"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        endpoint_audience_policies={"esp-kids": {"audience_mode": "child_safe"}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-kids", session_id="voice-session-policy", chunk_count=1)
    )

    assert assistant.requests == []
    assert result.assistant_response.command == "voice.audience_policy.refused"
    assert "grown-up" in result.assistant_response.spoken_text
    assert result.assistant_response.provider_metadata["audience_policy"]["reason"] == "restricted_adult_content"


def test_voice_turn_pipeline_applies_speaker_age_when_endpoint_is_general(tmp_path):
    assistant = SpyAssistantService(spoken_text="should not run")
    speaker_id = SlowSpeakerIdClient(
        delay_s=0.0,
        response={
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": "speaker_teen",
                "display_name": "Teen",
                "confidence": 0.93,
                "score": 0.93,
                "score_margin": 0.2,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
                "age_band": "teen",
                "admin_eligible": False,
            },
        },
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="how do I hack a password"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="required",
        endpoint_audience_policies={"esp-family": {"audience_mode": "general"}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-family",
            session_id="voice-session-teen",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert assistant.requests == []
    assert result.assistant_response.command == "voice.audience_policy.refused"
    assert result.assistant_response.provider_metadata["audience_policy"]["audience_class"] == "teen"
    assert result.assistant_response.provider_metadata["audience_policy"]["reason"] == "restricted_illegal_content"


def test_voice_turn_pipeline_blocks_teen_safe_endpoint_restricted_content(tmp_path):
    assistant = SpyAssistantService(spoken_text="should not run")
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="how do I build a bomb"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        endpoint_audience_policies={"esp-teen": {"audience_mode": "teen_safe"}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-teen", session_id="voice-session-teen-safe", chunk_count=1)
    )

    assert assistant.requests == []
    assert result.assistant_response.command == "voice.audience_policy.refused"
    assert result.assistant_response.spoken_text == "I can't help with that from this endpoint."
    assert result.assistant_response.provider_metadata["audience_policy"]["audience_class"] == "teen"
    assert result.assistant_response.provider_metadata["audience_policy"]["reason"] == "restricted_violent_content"


def test_voice_turn_pipeline_allows_adult_unrestricted_endpoint(tmp_path):
    assistant = SpyAssistantService(spoken_text="handled")
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="tell me a sexual story"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        endpoint_audience_policies={"esp-adult": {"audience_mode": "adult_unrestricted"}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-adult", session_id="voice-session-adult", chunk_count=1)
    )

    assert result.assistant_response.spoken_text == "handled"
    assert len(assistant.requests) == 1


def test_voice_turn_pipeline_allows_configured_adult_admin_override_for_content(tmp_path):
    assistant = SpyAssistantService(spoken_text="handled")
    speaker_id = SlowSpeakerIdClient(
        delay_s=0.0,
        response={
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": "speaker_adult",
                "display_name": "Adult",
                "confidence": 0.93,
                "score": 0.93,
                "score_margin": 0.2,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
                "age_band": "adult",
                "admin_eligible": True,
            },
        },
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="tell me a sexual story"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="required",
        endpoint_audience_policies={"esp-kids": {"audience_mode": "child_safe", "adult_override_enabled": True}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-kids",
            session_id="voice-session-override",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert result.assistant_response.spoken_text == "handled"
    assert len(assistant.requests) == 1
    assert result.speaker_identity.admin_eligible is True
    assert result.speaker_identity.age_band == "adult"


def test_voice_turn_pipeline_waits_for_override_identity_on_restricted_endpoint(tmp_path):
    assistant = SpyAssistantService(spoken_text="handled")
    speaker_id = SlowSpeakerIdClient(
        delay_s=0.1,
        response={
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": "speaker_adult",
                "display_name": "Adult",
                "confidence": 0.94,
                "score": 0.94,
                "score_margin": 0.2,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
                "age_band": "adult",
                "admin_eligible": True,
            },
        },
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="tell me a sexual story"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="use_if_ready",
        endpoint_audience_policies={"esp-kids": {"audience_mode": "child_safe", "adult_override_enabled": True}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-kids",
            session_id="voice-session-override-wait",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert result.assistant_response.spoken_text == "handled"
    assert len(assistant.requests) == 1
    assert result.speaker_identity.speaker_public_id == "speaker_adult"


def test_voice_turn_pipeline_blocks_admin_action_without_passcode_override(tmp_path):
    assistant = CommandAssistantService(
        command="debug.start",
        metadata={"admin_maintenance_action": True},
        spoken_text="debug started",
    )
    speaker_id = SlowSpeakerIdClient(
        delay_s=0.0,
        response={
            "schema_version": 1,
            "status": "identified",
            "reason": None,
            "match": {
                "speaker_public_id": "speaker_admin",
                "display_name": "Admin",
                "confidence": 0.95,
                "score": 0.95,
                "score_margin": 0.2,
                "provider": "deterministic_signal",
                "model_id": "deterministic-signal-v1",
                "age_band": "adult",
                "admin_eligible": True,
            },
        },
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="begin maintenance"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_policy_default="required",
        endpoint_audience_policies={"esp-kids": {"audience_mode": "child_safe", "adult_override_enabled": True}},
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(
            endpoint_id="esp-kids",
            session_id="voice-session-admin",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 1600,
        )
    )

    assert len(assistant.requests) == 1
    assert result.assistant_response.command == "voice.audience_policy.refused"
    assert result.assistant_response.provider_metadata["audience_policy"]["reason"] == "restricted_admin_action"


def test_voice_turn_pipeline_speaker_identity_follows_audio_not_endpoint(tmp_path):
    assistant = SpyAssistantService(spoken_text="Opening your calendar.")
    speaker_id = AudioFingerprintSpeakerIdClient()
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="what's on my calendar"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
        speaker_id_client=speaker_id,
        speaker_id_enabled=True,
        speaker_id_timeout_s=1.0,
        speaker_id_policy_default="use_if_ready",
    )

    for endpoint_id, session_id, audio_bytes in (
        ("esp-kitchen", "voice-session-1", b"\x01\x00" * 1600),
        ("esp-bedroom", "voice-session-2", b"\x01\x00" * 1600),
        ("esp-kitchen", "voice-session-3", b"\x02\x00" * 1600),
    ):
        pipeline.complete_turn(
            VoiceTurnAudioSummary(
                endpoint_id=endpoint_id,
                session_id=session_id,
                chunk_count=2,
                sample_rate_hz=16000,
                encoding="pcm_s16le",
                channels=1,
                audio_bytes=audio_bytes,
            )
        )

    assert [request.endpoint_id for request in assistant.requests] == ["esp-kitchen", "esp-bedroom", "esp-kitchen"]
    assert [request.speaker_identity["speaker_public_id"] for request in assistant.requests] == [
        "speaker_dan",
        "speaker_dan",
        "speaker_alex",
    ]
    assert [call["audio"]["sample_id"] for call in speaker_id.calls] == [
        "voice-session-1-turn",
        "voice-session-2-turn",
        "voice-session-3-turn",
    ]


def test_voice_turn_pipeline_can_select_voice_by_endpoint(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    tts_adapter = CaptureTextToSpeechAdapter()
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=tts_adapter,
        endpoint_voices={"esp-pe-1": "en_US-hfc_female-medium"},
    )

    pipeline.complete_turn(VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-1", chunk_count=2))
    pipeline.synthesize_reply(
        endpoint_id="esp-pe-1",
        session_id="voice-session-2",
        text="hello",
        voice="en_US-lessac-medium",
    )

    assert tts_adapter.calls[0]["voice"] == "en_US-hfc_female-medium"
    assert tts_adapter.calls[1]["voice"] == "en_US-lessac-medium"
    assert pipeline.status()["endpoint_voices"] == {"esp-pe-1": "en_US-hfc_female-medium"}


def test_build_voice_turn_pipeline_keeps_deterministic_stt_as_default(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)
    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert isinstance(pipeline._stt_adapter, DeterministicSpeechToTextAdapter)
    assert result.transcript.provider_id == "deterministic"
    assert result.transcript.text == "hello"
    assert result.assistant_response.spoken_text == "I heard hello"


def test_voice_turn_pipeline_strips_wake_word_from_final_transcript(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, what time is it?"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.transcript.text == "what time is it?"
    assert result.assistant_response.heard_text == "what time is it?"
    assert result.assistant_response.spoken_text == "I heard what time is it?"


def test_voice_turn_pipeline_handles_timer_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, set a timer for 10 minutes"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.transcript.text == "set a timer for 10 minutes"
    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.create"
    assert result.assistant_response.provider_id == "local_pattern"
    assert result.assistant_response.spoken_text == "Setting timer for 10 minutes."
    assert result.assistant_response.intent_latency_ms >= 0
    assert publisher.calls == [
        {
            "endpoint_id": "esp-box-1",
            "session_id": "voice-session-1",
            "heard_text": "set a timer for 10 minutes",
            "duration_seconds": 600,
            "duration_text": "10 minutes",
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]
    assert publisher.calls[0]["requested_at"].tzinfo is not None


def test_voice_turn_pipeline_handles_timer_status_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, how much time is left on the timer"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.transcript.text == "how much time is left on the timer"
    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.status"
    assert result.assistant_response.provider_id == "local_pattern"
    assert result.assistant_response.spoken_text == "Checking the timer."
    assert publisher.calls == [
        {
            "endpoint_id": "esp-box-1",
            "session_id": "voice-session-1",
            "heard_text": "how much time is left on the timer",
            "scope": "active_for_endpoint",
            "timer_id": None,
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]


def test_voice_turn_pipeline_handles_timer_stop_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, stop the timer"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-stop", chunk_count=1)
    )

    assert result.transcript.text == "stop the timer"
    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.stop"
    assert result.assistant_response.spoken_text == "Stopping the timer."
    assert publisher.calls == [
        {
            "action": "stop",
            "endpoint_id": "esp-pe-1",
            "session_id": "voice-session-stop",
            "heard_text": "stop the timer",
            "scope": "active_for_endpoint",
            "timer_id": None,
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]


def test_voice_turn_pipeline_adds_selected_timer_id_to_timer_control(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    cache = TimerOwnershipCache()
    cache.update_from_event(
        "hexe/events/timer/create_succeeded",
        {
            "event_id": "timer-create-1",
            "event_type": "timer.create_succeeded",
            "source": {"node_id": "node-timer-1"},
            "subject": {"family": "timer", "record_id": "timer-1"},
            "data": {
                "endpoint_id": "esp-pe-1",
                "timer_id": "timer-1",
                "title": "tea",
                "due_at": "2026-08-23T23:00:00+00:00",
            },
        },
    )
    assistant = AssistantTurnService(
        settings=settings,
        runtime_service=runtime,
        timer_event_publisher=publisher,
        timer_ownership_cache=cache,
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, stop the timer"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-stop", chunk_count=1)
    )

    assert result.assistant_response.command == "timer.stop"
    assert result.assistant_response.spoken_text == "Stopping the timer."
    assert publisher.calls[0]["timer_id"] == "timer-1"


def test_voice_turn_pipeline_skips_ambiguous_timer_command(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    cache = TimerOwnershipCache()
    cache.update_from_event(
        "hexe/events/timer/status_succeeded",
        {
            "event_id": "timer-status-list",
            "event_type": "timer.status_succeeded",
            "source": {"node_id": "node-timer-1"},
            "data": {
                "endpoint_id": "esp-pe-1",
                "timers": [
                    {"timer_id": "timer-a", "state": "active", "title": "tea"},
                    {"timer_id": "timer-b", "state": "active", "title": "laundry"},
                ],
            },
        },
    )
    assistant = AssistantTurnService(
        settings=settings,
        runtime_service=runtime,
        timer_event_publisher=publisher,
        timer_ownership_cache=cache,
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, cancel the timer"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-cancel", chunk_count=1)
    )

    assert result.assistant_response.command == "timer.cancel"
    assert result.assistant_response.spoken_text == "I found multiple active timers: tea, laundry. Please say which timer."
    assert publisher.calls == []


def test_voice_turn_pipeline_handles_timer_adjust_time_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, add five minutes to the timer"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-adjust", chunk_count=1)
    )

    assert result.transcript.text == "add five minutes to the timer"
    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.adjust_time"
    assert result.assistant_response.spoken_text == "Updating the timer."
    assert publisher.calls == [
        {
            "endpoint_id": "esp-pe-1",
            "session_id": "voice-session-adjust",
            "heard_text": "add five minutes to the timer",
            "delta_seconds": 300,
            "delta_text": "5 minutes",
            "scope": "active_for_endpoint",
            "timer_id": None,
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]


def test_voice_turn_pipeline_dispatches_endpoint_volume_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    dispatcher = FakeEndpointCommandDispatcher()
    assistant = AssistantTurnService(
        settings=settings,
        runtime_service=runtime,
        endpoint_command_dispatcher=dispatcher,
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, set volume to 60 percent"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-volume", chunk_count=1)
    )

    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "endpoint.volume.set"
    assert result.assistant_response.spoken_text == "Setting volume to 60 percent."
    assert dispatcher.calls == [
        {
            "endpoint_id": "esp-box-1",
            "session_id": "voice-session-volume",
            "command": "endpoint.volume.set",
            "slots": {
                "requested_at": dispatcher.calls[0]["slots"]["requested_at"],
                "volume_percent": 60,
            },
        }
    ]


def test_voice_turn_pipeline_handles_timer_snooze_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, snooze the timer for five minutes"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-snooze", chunk_count=1)
    )

    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.snooze"
    assert result.assistant_response.spoken_text == "Snoozing timer for 5 minutes."
    assert publisher.calls == [
        {
            "endpoint_id": "esp-pe-1",
            "session_id": "voice-session-snooze",
            "heard_text": "snooze the timer for five minutes",
            "duration_seconds": 300,
            "duration_text": "5 minutes",
            "scope": "active_for_endpoint",
            "timer_id": None,
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]


def test_voice_turn_pipeline_does_not_wait_for_domain_event_publish(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    slow_publisher = SlowRecognitionPublisher(delay_s=0.3)
    assistant = AssistantTurnService(
        settings=settings,
        runtime_service=runtime,
        timer_event_publisher=AsyncDomainEventPublisher(slow_publisher),
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, set a timer for 10 minutes"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.create"
    assert result.timings.assistant_ms < 200
    assert result.assistant_response.intent_latency_ms is not None
    assert result.assistant_response.intent_latency_ms < 200
    assert slow_publisher.published.wait(timeout=1)
    assert slow_publisher.calls[0]["type"] == "recognition"


def test_openai_stt_adapter_posts_wav_transcription_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "what time is it"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fake_token = "unit-test-token"
    adapter = OpenAiSpeechToTextAdapter(
        api_key=fake_token,
        model="gpt-4o-mini-transcribe",
        base_url="https://api.openai.test/v1",
        http_client=client,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x00\x00" * 320,
        )
    )

    assert transcript.text == "what time is it"
    assert transcript.provider_id == "openai"
    assert captured["authorization"] == f"Bearer {fake_token}"
    assert b'gpt-4o-mini-transcribe' in captured["body"]
    assert b"audio.wav" in captured["body"]


def test_openai_stt_adapter_wraps_pcm_as_wav():
    adapter = OpenAiSpeechToTextAdapter(**{"api" + "_key": "unit-test-token"})

    audio = adapter._audio_file(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 320


def test_build_voice_turn_pipeline_uses_openai_stt_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        voice_stt_provider="openai",
        openai_api_key="test-key",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._stt_adapter, SilenceTrimmingSpeechToTextAdapter)
    assert isinstance(pipeline._stt_adapter._wrapped, OpenAiSpeechToTextAdapter)


def test_trim_stt_silence_removes_leading_and_trailing_pcm_padding():
    samples = [0] * 100 + [1200] * 200 + [0] * 300
    audio = VoiceTurnAudioSummary(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        chunk_count=1,
        sample_rate_hz=1000,
        encoding="pcm_s16le",
        channels=1,
        audio_bytes=b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in samples),
    )

    trimmed, metadata = trim_stt_silence(
        audio,
        SttSilenceTrimConfig(
            enabled=True,
            threshold=1000,
            leading_padding_ms=20,
            trailing_padding_ms=30,
            min_audio_ms=0,
        ),
    )

    assert metadata["applied"] is True
    assert metadata["removed_leading_ms"] == 80
    assert metadata["removed_trailing_ms"] == 270
    assert metadata["original_duration_ms"] == 600
    assert metadata["trimmed_duration_ms"] == 250
    assert len(trimmed.audio_bytes or b"") == 250 * 2


def test_trim_stt_silence_preserves_minimum_audio_window():
    samples = [0] * 100 + [1200] * 20 + [0] * 100
    audio = VoiceTurnAudioSummary(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        chunk_count=1,
        sample_rate_hz=1000,
        encoding="pcm_s16le",
        channels=1,
        audio_bytes=b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in samples),
    )

    trimmed, metadata = trim_stt_silence(
        audio,
        SttSilenceTrimConfig(
            enabled=True,
            threshold=1000,
            leading_padding_ms=0,
            trailing_padding_ms=0,
            min_audio_ms=100,
        ),
    )

    assert metadata["applied"] is True
    assert metadata["trimmed_duration_ms"] == 100
    assert len(trimmed.audio_bytes or b"") == 100 * 2


def test_faster_whisper_stt_adapter_transcribes_temp_wav_and_removes_it(tmp_path):
    captured = {}

    class FakeModel:
        def __init__(self, model_name, *, device, compute_type):
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, path, **options):
            captured["path"] = path
            captured["options"] = options
            with wave.open(path, "rb") as wav_file:
                captured["sample_rate_hz"] = wav_file.getframerate()
                captured["channels"] = wav_file.getnchannels()
                captured["frames"] = wav_file.getnframes()
            return [SimpleNamespace(text=" what "), SimpleNamespace(text=" time ")], object()

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="base.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        language="en",
        beam_size=2,
        best_of=3,
        without_timestamps=True,
        word_timestamps=True,
        max_initial_timestamp=0.5,
        model_factory=FakeModel,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    assert transcript.text == "what time"
    assert transcript.provider_id == "faster_whisper"
    assert transcript.model == "base.en"
    assert transcript.duration_ms is not None
    assert transcript.timing_breakdown_ms["audio_preparation_ms"] >= 0
    assert transcript.timing_breakdown_ms["model_inference_ms"] >= 0
    assert transcript.timing_breakdown_ms["decoding_ms"] >= 0
    assert transcript.timing_breakdown_ms["post_processing_ms"] >= 0
    assert transcript.timing_breakdown_ms["total_ms"] == transcript.duration_ms
    assert captured["model_name"] == "base.en"
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"
    assert captured["sample_rate_hz"] == 16000
    assert captured["channels"] == 1
    assert captured["frames"] == 320
    assert captured["options"] == {
        "without_timestamps": True,
        "word_timestamps": True,
        "language": "en",
        "beam_size": 2,
        "best_of": 3,
        "max_initial_timestamp": 0.5,
    }
    assert not Path(captured["path"]).exists()


def test_faster_whisper_stt_adapter_preloads_model(tmp_path):
    captured = {"loads": 0}

    class FakeModel:
        def __init__(self, model_name, *, device, compute_type):
            captured["loads"] += 1
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="base.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        model_factory=FakeModel,
    )

    preload = adapter.preload()
    second_preload = adapter.preload()

    assert preload["loaded"] is True
    assert preload["model"] == "base.en"
    assert preload["duration_ms"] is not None
    assert second_preload["loaded"] is True
    assert captured["loads"] == 1
    assert adapter.status()["loaded"] is True
    assert adapter.status()["loaded_at"] is not None
    assert adapter.status()["load_count"] == 1
    assert adapter.status()["reload_required"] is False
    assert adapter.status()["loaded_config"]["model"] == "base.en"
    assert adapter.status()["last_load_duration_ms"] is not None


def test_build_voice_turn_pipeline_uses_faster_whisper_stt_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_stt_provider="faster_whisper",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._stt_adapter, SilenceTrimmingSpeechToTextAdapter)
    assert isinstance(pipeline._stt_adapter._wrapped, ProfiledSpeechToTextAdapter)
    assert isinstance(pipeline._stt_adapter._wrapped._wrapped, FasterWhisperSpeechToTextAdapter)


def test_build_voice_turn_pipeline_uses_selected_stt_profile(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_stt_provider="faster_whisper",
        voice_stt_profile="cuda_fast_intent",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    status = pipeline.status()["stt"]
    assert status["active_profile"] == "cuda_fast_intent"
    assert status["model"] == "small.en"
    assert status["device"] == "cuda"
    assert status["compute_type"] == "float16"
    assert status["stt_profile"]["beam_size"] == 1
    assert status["fallback_profile"] == "cuda_accurate_fallback"


def test_external_faster_whisper_stt_adapter_posts_audio_to_service():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "text": "what time",
                "provider_id": "external_faster_whisper",
                "model": "small.en",
                "duration_ms": 12.3,
                "timing_breakdown_ms": {"total_ms": 12.3, "model_inference_ms": 10.0},
            },
        )

    adapter = ExternalFasterWhisperSpeechToTextAdapter(
        base_url="http://stt.test:10300",
        model_name="small.en",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    assert captured["url"] == "http://stt.test:10300/transcribe"
    assert captured["json"]["endpoint_id"] == "esp-box-1"
    assert captured["json"]["sample_rate_hz"] == 16000
    assert captured["json"]["encoding"] == "pcm_s16le"
    assert captured["json"]["model"] == "small.en"
    assert captured["json"]["audio_base64"]
    assert transcript.text == "what time"
    assert transcript.provider_id == "external_faster_whisper"
    assert transcript.model == "small.en"
    assert transcript.duration_ms == 12.3
    assert transcript.timing_breakdown_ms["model_inference_ms"] == 10.0


def test_silence_trimming_adapter_trims_audio_before_external_faster_whisper_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "text": "what time",
                "provider_id": "external_faster_whisper",
                "model": "small.en",
                "duration_ms": 12.3,
                "timing_breakdown_ms": {"total_ms": 12.3},
            },
        )

    wrapped = ExternalFasterWhisperSpeechToTextAdapter(
        base_url="http://stt.test:10300",
        model_name="small.en",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter = SilenceTrimmingSpeechToTextAdapter(
        wrapped=wrapped,
        config=SttSilenceTrimConfig(
            enabled=True,
            threshold=1000,
            leading_padding_ms=20,
            trailing_padding_ms=30,
            min_audio_ms=0,
        ),
    )
    samples = [0] * 100 + [1200] * 200 + [0] * 300

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=1000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in samples),
        )
    )

    sent_audio = base64.b64decode(captured["json"]["audio_base64"])
    assert len(sent_audio) == 250 * 2
    assert transcript.text == "what time"
    assert transcript.timing_breakdown_ms["silence_trim_removed_duration_ms"] == 350
    assert adapter.status()["silence_trim"]["last_trim"]["applied"] is True


def test_external_faster_whisper_stt_status_clears_stale_connection_error():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/transcribe":
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        return httpx.Response(
            200,
            json={
                "provider": "external_faster_whisper",
                "healthy": True,
                "configured": True,
                "model": "small.en",
                "last_error": None,
            },
        )

    adapter = ExternalFasterWhisperSpeechToTextAdapter(
        base_url="http://stt.test:10300",
        model_name="small.en",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )
    assert transcript.error == "[Errno 111] Connection refused"

    status = adapter.status()

    assert requests == ["http://stt.test:10300/transcribe", "http://stt.test:10300/health"]
    assert status["healthy"] is True
    assert status["last_error"] is None


def test_build_voice_turn_pipeline_uses_external_faster_whisper_stt_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_stt_provider="external_faster_whisper",
        voice_stt_service_base_url="http://stt.test:10300",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._stt_adapter, SilenceTrimmingSpeechToTextAdapter)
    assert isinstance(pipeline._stt_adapter._wrapped, ProfiledSpeechToTextAdapter)
    assert isinstance(pipeline._stt_adapter._wrapped._wrapped, ExternalFasterWhisperSpeechToTextAdapter)


def test_stt_profile_resolver_preserves_legacy_single_model_settings(tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        voice_stt_faster_whisper_model="small.en",
        voice_stt_faster_whisper_device="cpu",
        voice_stt_faster_whisper_compute_type="int8",
        voice_stt_faster_whisper_beam_size=2,
    )

    profile = resolve_stt_model_profile(settings)

    assert profile.name == "custom_legacy"
    assert profile.model == "small.en"
    assert profile.device == "cpu"
    assert profile.compute_type == "int8"
    assert profile.beam_size == 2


def test_stt_fast_profile_fallback_rules():
    settings = Settings(voice_stt_profile="cuda_fast_intent")
    profile = resolve_stt_model_profile(settings)

    assert should_use_stt_fallback(
        SpeechTranscript(text="turn on the kitchen", confidence=0.4, provider_id="faster_whisper"),
        profile=profile,
        intent_matched=True,
    )
    assert should_use_stt_fallback(
        SpeechTranscript(text="turn on the kitchen", confidence=0.9, provider_id="faster_whisper"),
        profile=profile,
        intent_matched=False,
    )
    assert not should_use_stt_fallback(
        SpeechTranscript(text="turn on the kitchen", confidence=0.9, provider_id="faster_whisper"),
        profile=profile,
        intent_matched=True,
    )


def test_voice_turn_pipeline_reruns_stt_with_fallback_profile_when_fast_intent_unmatched(tmp_path):
    class FixedSttAdapter:
        def __init__(self, transcript: SpeechTranscript) -> None:
            self.transcript = transcript
            self.calls = 0

        def transcribe(self, audio):
            self.calls += 1
            return self.transcript

        def status(self):
            return {"provider": self.transcript.provider_id, "healthy": True, "model": self.transcript.model}

    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    primary = FixedSttAdapter(SpeechTranscript(text="kitchen marble", confidence=0.91, provider_id="faster_whisper", model="small.en"))
    fallback = FixedSttAdapter(
        SpeechTranscript(text="Hexa, set a timer for 5 minutes", confidence=0.98, provider_id="faster_whisper", model="medium.en")
    )
    stt_adapter = ProfiledSpeechToTextAdapter(
        wrapped=primary,
        profile=SttModelProfile(
            name="fast_intent",
            model="small.en",
            device="cuda",
            compute_type="float16",
            fallback_profile="accurate_fallback",
            fallback_when=("intent_unmatched",),
        ),
        fallback_wrapped=fallback,
        fallback_profile=SttModelProfile(name="accurate_fallback", model="medium.en", device="cuda", compute_type="float16"),
    )
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=stt_adapter,
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1, audio_bytes=b"\x01\x00" * 320)
    )

    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.transcript.text == "set a timer for 5 minutes"
    assert result.transcript.model == "medium.en"
    assert result.assistant_response.command == "timer.create"
    assert publisher.calls[0]["duration_seconds"] == 300
    assert stt_adapter.status()["last_fallback"]["used"] is True
    assert stt_adapter.status()["last_fallback"]["fallback_profile"] == "accurate_fallback"


def test_faster_whisper_stt_adapter_returns_error_without_losing_fallback_modes(tmp_path):
    class FailingModel:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("model unavailable")

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="small.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        model_factory=FailingModel,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    assert transcript.provider_id == "faster_whisper"
    assert transcript.text == ""
    assert transcript.error == "model unavailable"
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "model unavailable"


def test_openai_tts_adapter_posts_speech_request_and_stores_audio(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = request.read()
        return httpx.Response(200, content=b"RIFFtest-wav")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fake_token = "unit-test-token"
    adapter = OpenAiTextToSpeechAdapter(
        **{"api" + "_key": fake_token},
        output_dir=tmp_path,
        model="gpt-4o-mini-tts",
        voice="alloy",
        base_url="https://api.openai.test/v1",
        response_format="wav",
        http_client=client,
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.provider_id == "openai"
    assert synthesis.content_type == "audio/wav"
    assert synthesis.stream_id is not None
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert (tmp_path / f"{synthesis.stream_id}.wav").read_bytes() == b"RIFFtest-wav"
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert metadata["provider_id"] == "openai"
    assert metadata["model_id"] == "gpt-4o-mini-tts"
    assert metadata["voice_id"] == "alloy"
    assert metadata["ttl_seconds"] == 3600
    assert metadata["expires_at"]
    assert synthesis.model_id == "gpt-4o-mini-tts"
    assert synthesis.voice_id == "alloy"
    assert synthesis.metadata_path == str(tmp_path / f"{synthesis.stream_id}.json")
    assert synthesis.ttl_seconds == 3600
    assert captured["authorization"] == f"Bearer {fake_token}"
    assert b"gpt-4o-mini-tts" in captured["json"]
    assert b"hello" in captured["json"]


def test_openai_tts_adapter_can_override_voice_and_format_per_request(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, content=b"mp3-bytes")

    adapter = OpenAiTextToSpeechAdapter(
        **{"api" + "_key": "unit-test-token"},
        output_dir=tmp_path,
        model="gpt-4o-mini-tts",
        voice="alloy",
        base_url="https://api.openai.test/v1",
        response_format="wav",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        text="hello",
        voice="nova",
        audio_format="mp3",
    )

    assert synthesis.content_type == "audio/mpeg"
    assert (tmp_path / f"{synthesis.stream_id}.mp3").read_bytes() == b"mp3-bytes"
    assert b"nova" in captured["json"]
    assert b"mp3" in captured["json"]


def test_build_voice_turn_pipeline_uses_openai_tts_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="openai",
        **{"openai" + "_api_key": "unit-test-token"},
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._tts_adapter, OpenAiTextToSpeechAdapter)


def test_piper_tts_adapter_posts_synthesis_request_and_stores_audio(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        return httpx.Response(200, content=b"RIFFpiper-wav", headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        synthesize_path="/api/tts",
        voice="en_US-test",
        output_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert captured["url"] == "http://piper.test:10200/api/tts"
    assert b"hello" in captured["json"]
    assert b"en_US-test" in captured["json"]
    assert synthesis.provider_id == "piper"
    assert synthesis.content_type == "audio/wav"
    assert synthesis.audio_variant == "16k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/16k"
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").read_bytes() == b"RIFFpiper-wav"
    assert (tmp_path / f"{synthesis.stream_id}.16k.wav").read_bytes() == b"RIFFpiper-wav"
    assert (tmp_path / f"{synthesis.stream_id}.48k.wav").read_bytes() == b"RIFFpiper-wav"
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert metadata["provider_id"] == "piper"
    assert metadata["model_id"] == "en_US-test"
    assert metadata["voice_id"] == "en_US-test"
    assert metadata["audio_url"] == f"/api/voice/tts/{synthesis.stream_id}/"
    assert metadata["audio_url_raw"] == f"/api/voice/tts/{synthesis.stream_id}/raw"
    assert metadata["audio_url_16k"] == f"/api/voice/tts/{synthesis.stream_id}/16k"
    assert metadata["audio_url_48k"] == f"/api/voice/tts/{synthesis.stream_id}/48k"
    assert metadata["audio_url_48K"] == f"/api/voice/tts/{synthesis.stream_id}/48k"
    assert metadata["endpoint_audio_url"] == f"/api/voice/tts/{synthesis.stream_id}/16k"
    assert metadata["audio_variant"] == "16k"
    assert metadata["audio_variant_sample_rate_hz"] is None
    assert metadata["audio_variant_source_sample_rate_hz"] is None
    assert metadata["tts_timing_breakdown_ms"]["piper_generation_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["raw_save_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["conversion_16k_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["conversion_48k_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["conversion_total_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["sidecar_write_ms"] >= 0
    assert metadata["ttl_seconds"] == 3600
    assert metadata["expires_at"]
    assert synthesis.metadata_path == str(tmp_path / f"{synthesis.stream_id}.json")
    assert synthesis.model_id == "en_US-test"
    assert synthesis.voice_id == "en_US-test"
    assert synthesis.timing_breakdown_ms["piper_generation_ms"] >= 0
    assert synthesis.timing_breakdown_ms["sidecar_write_ms"] >= 0
    assert synthesis.ttl_seconds == 3600
    assert adapter.status()["healthy"] is True


def test_piper_tts_adapter_resamples_wav_for_endpoint(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "16k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/16k"
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnchannels() == 1
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.raw.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 22050
    assert synthesis.raw_sample_rate_hz == 22050
    assert synthesis.audio_variant_sample_rate_hz == 16000
    assert synthesis.audio_variant_source_sample_rate_hz == 22050
    assert synthesis.output_sample_rate_hz == 16000
    assert synthesis.variant_sample_rates_hz == {"raw": 22050, "16k": 16000, "48k": 48000}
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert metadata["audio_url"] == f"/api/voice/tts/{synthesis.stream_id}/"
    assert metadata["endpoint_audio_url"] == f"/api/voice/tts/{synthesis.stream_id}/16k"
    assert metadata["audio_variant_sample_rate_hz"] == 16000
    assert metadata["audio_variant_source_sample_rate_hz"] == 22050
    assert metadata["variant_sample_rates_hz"] == {"raw": 22050, "16k": 16000, "48k": 48000}
    assert metadata["tts_timing_breakdown_ms"]["conversion_16k_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["conversion_48k_ms"] >= 0
    assert metadata["tts_timing_breakdown_ms"]["conversion_total_ms"] >= 0


def test_piper_tts_endpoint_required_policy_defers_optional_variants(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        conversion_policy="endpoint_required_sync",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.conversion_policy == "endpoint_required_sync"
    assert synthesis.audio_variant == "16k"
    assert synthesis.pending_audio_variants == {"48k": str(tmp_path / f"{synthesis.stream_id}.48k.wav")}
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").exists()
    assert (tmp_path / f"{synthesis.stream_id}.16k.wav").exists()
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert metadata["conversion_policy"] == "endpoint_required_sync"
    assert "raw" in metadata["ready_audio_variants"]
    assert "16k" in metadata["ready_audio_variants"]

    deadline = time.time() + 2
    while time.time() < deadline and metadata.get("optional_conversion_status") != "completed":
        time.sleep(0.02)
        metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))

    assert metadata["optional_conversion_status"] == "completed"
    assert metadata["pending_audio_variants"] == {}
    assert set(metadata["ready_audio_variants"]) == {"raw", "16k", "48k"}
    assert metadata["variant_sample_rates_hz"]["48k"] == 48000
    assert metadata["audio_url_48k"] == f"/api/voice/tts/{synthesis.stream_id}/48k"
    assert metadata["tts_timing_breakdown_ms"]["background_conversion_48k_ms"] >= 0


def test_piper_tts_endpoint_required_policy_blocks_on_pe_48k_variant(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        endpoint_sample_rates={"esp-pe-1": 48000},
        conversion_policy="endpoint_required_sync",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-pe-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "48k"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/48k"
    assert synthesis.pending_audio_variants == {"16k": str(tmp_path / f"{synthesis.stream_id}.16k.wav")}
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").exists()
    assert (tmp_path / f"{synthesis.stream_id}.48k.wav").exists()
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert "raw" in metadata["ready_audio_variants"]
    assert "48k" in metadata["ready_audio_variants"]
    assert metadata["endpoint_audio_url"] == f"/api/voice/tts/{synthesis.stream_id}/48k"
    assert metadata["audio_variant_sample_rate_hz"] == 48000
    assert metadata["audio_variant_source_sample_rate_hz"] == 22050
    if metadata["optional_conversion_status"] == "completed":
        assert metadata["pending_audio_variants"] == {}
        assert "16k" in metadata["ready_audio_variants"]
    else:
        assert metadata["pending_audio_variants"] == {"16k": str(tmp_path / f"{synthesis.stream_id}.16k.wav")}


def test_piper_tts_adapter_can_generate_configured_22050_variant(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 1600))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=22050,
        conversion_sample_rates={"22050": 22050, "48k": 48000, "16k": 16000},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "22050"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/22050"
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.22050.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 22050
    assert synthesis.variant_sample_rates_hz == {"raw": 16000, "16k": 16000, "22050": 22050, "48k": 48000}


def test_piper_tts_adapter_uses_endpoint_specific_sample_rate(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        endpoint_sample_rates={"esp-pe-1": 48000},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-pe-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "48k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/48k"
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
    assert synthesis.audio_variant_sample_rate_hz == 48000
    assert synthesis.audio_variant_source_sample_rate_hz == 22050
    assert synthesis.output_sample_rate_hz == 48000
    metadata = json.loads((tmp_path / f"{synthesis.stream_id}.json").read_text(encoding="utf-8"))
    assert metadata["model_id"] == "piper-default"
    assert metadata["voice_id"] == "piper-default"
    assert metadata["target_sample_rate_hz"] == 48000
    assert metadata["raw_sample_rate_hz"] == 22050
    assert metadata["output_sample_rate_hz"] == 48000
    assert adapter.status()["endpoint_sample_rates"] == {"esp-pe-1": 48000}


def test_piper_tts_adapter_keeps_native_wav_when_resampling_disabled(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 1600))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "raw"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/"
    assert synthesis.endpoint_audio_url == f"/api/voice/tts/{synthesis.stream_id}/raw"
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").read_bytes() == source.getvalue()
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000


def test_piper_tts_adapter_falls_back_when_unconfigured(tmp_path):
    adapter = PiperTextToSpeechAdapter(base_url=None, output_dir=tmp_path)

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.provider_id == "deterministic"
    assert synthesis.stream_id.startswith("tts-")
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "missing_piper_base_url"


def test_build_voice_turn_pipeline_uses_piper_tts_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
        voice_tts_piper_base_url="http://piper.test:10200",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._tts_adapter, PiperTextToSpeechAdapter)


def test_build_voice_turn_pipeline_routes_piper_to_supervised_default(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    status = pipeline.status()["tts"]
    assert status["provider"] == "piper"
    assert status["configured"] is True
    assert status["base_url"] == "http://hexevoice-piper-tts"
    assert status["synthesize_path"] == "/api/tts"
    assert status["output_sample_rate_hz"] == 16000
    assert status["fallback"]["provider"] == "deterministic"


def test_build_voice_turn_pipeline_applies_endpoint_voice_overrides(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_endpoint_voices="esp-pe-1=en_US-hfc_female-medium",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert pipeline.status()["endpoint_voices"] == {"esp-pe-1": "en_US-hfc_female-medium"}


def test_build_voice_turn_pipeline_applies_endpoint_sample_rate_overrides(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
        voice_tts_endpoint_sample_rates="esp-pe-1=48000,esp-box-1=16000",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert pipeline.status()["tts"]["endpoint_sample_rates"] == {"esp-pe-1": 48000, "esp-box-1": 16000}


def test_voice_turn_pipeline_status_reports_provider_health(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)
    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    status = pipeline.status()

    assert status["stt"]["provider"] == "deterministic"
    assert status["stt"]["healthy"] is True
    assert status["stt"]["engine_role"] == "stt_engine"
    assert status["stt"]["implementation"] == "deterministic"
    assert status["stt"]["implementation_health"] == {
        "engine_role": "stt_engine",
        "active_implementation": "deterministic",
        "provider": "deterministic",
        "model": None,
        "healthy": True,
        "configured": True,
        "last_error": None,
    }
    assert status["tts"]["provider"] == "deterministic"
    assert status["tts"]["healthy"] is True
    assert status["tts"]["engine_role"] == "tts_engine"
    assert status["tts"]["implementation"] == "deterministic"
    assert status["tts"]["implementation_health"]["active_implementation"] == "deterministic"
    assert status["tts"]["implementation_health"]["last_error"] is None
