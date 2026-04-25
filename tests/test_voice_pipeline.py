from hexevoice.assistant import AssistantTurnService
from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.voice import (
    DeterministicSpeechToTextAdapter,
    DeterministicTextToSpeechAdapter,
    VoiceTurnAudioSummary,
    VoiceTurnPipeline,
)


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
    assert result.assistant_response.command == "status"
    assert "lab-voice is not ready" in result.assistant_response.spoken_text
    assert result.tts.content_type == "audio/wav"
    assert result.tts.stream_id.startswith("tts-")
