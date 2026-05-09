from pathlib import Path
import json
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    node_name: str = Field(default="hexevoice", alias="NODE_NAME")
    node_type: str = Field(default="voice-node", alias="NODE_TYPE")
    node_software_version: str = Field(default="0.1.0", alias="NODE_SOFTWARE_VERSION")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=9000, alias="API_PORT")
    public_api_base_url: str | None = Field(default=None, alias="PUBLIC_API_BASE_URL")
    public_ui_base_url: str | None = Field(default=None, alias="PUBLIC_UI_BASE_URL")
    runtime_dir: Path = Field(default=Path("runtime"), alias="RUNTIME_DIR")
    backend_log_path: Path | None = Field(default=None, alias="BACKEND_LOG_PATH")
    voice_record_log_path: Path | None = Field(default=None, alias="VOICE_RECORD_LOG_PATH")
    backend_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="BACKEND_LOG_LEVEL",
    )
    backend_log_backup_days: int = Field(default=14, alias="BACKEND_LOG_BACKUP_DAYS", ge=1)
    firmware_artifact_dir: Path | None = Field(default=None, alias="FIRMWARE_ARTIFACT_DIR")
    onboarding_state_path: Path | None = Field(default=None, alias="ONBOARDING_STATE_PATH")
    endpoint_registry_path: Path | None = Field(default=None, alias="ENDPOINT_REGISTRY_PATH")
    voice_intent_registry_path: Path | None = Field(default=None, alias="VOICE_INTENT_REGISTRY_PATH")
    endpoint_media_dir: Path | None = Field(default=None, alias="ENDPOINT_MEDIA_DIR")
    endpoint_stale_after_seconds: int = Field(default=60, alias="ENDPOINT_STALE_AFTER_SECONDS", ge=1)
    bootstrap_mqtt_port: int = Field(default=1884, alias="BOOTSTRAP_MQTT_PORT")
    bootstrap_topic: str = Field(default="hexe/bootstrap/core", alias="BOOTSTRAP_TOPIC")
    provider_id: str = Field(default="voice", alias="PROVIDER_ID")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    voice_wake_provider: Literal["openwakeword", "supervised_openwakeword", "deterministic"] = Field(
        default="openwakeword",
        alias="VOICE_WAKE_PROVIDER",
    )
    voice_wake_threshold: float = Field(default=0.5, alias="VOICE_WAKE_THRESHOLD", ge=0.0, le=1.0)
    voice_wake_models: str | None = Field(default=None, alias="VOICE_WAKE_MODELS")
    voice_wake_auto_download_models: bool = Field(default=False, alias="VOICE_WAKE_AUTO_DOWNLOAD_MODELS")
    voice_wake_preload: bool = Field(default=False, alias="VOICE_WAKE_PRELOAD")
    voice_wake_enable_speex_noise_suppression: bool = Field(
        default=False,
        alias="VOICE_WAKE_ENABLE_SPEEX_NOISE_SUPPRESSION",
    )
    voice_wake_vad_threshold: float | None = Field(
        default=None,
        alias="VOICE_WAKE_VAD_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    voice_wake_buffer_ms: int = Field(default=1280, alias="VOICE_WAKE_BUFFER_MS", ge=80)
    voice_wake_prediction_frame_ms: int = Field(default=80, alias="VOICE_WAKE_PREDICTION_FRAME_MS", ge=80)
    voice_wake_service_host: str = Field(default="127.0.0.1", alias="VOICE_WAKE_SERVICE_HOST")
    voice_wake_service_port: int = Field(default=10400, alias="VOICE_WAKE_SERVICE_PORT")
    voice_wake_service_timeout_s: float = Field(default=0.05, alias="VOICE_WAKE_SERVICE_TIMEOUT_S", gt=0)
    voice_wake_recordings_enabled: bool = Field(default=False, alias="VOICE_WAKE_RECORDINGS_ENABLED")
    voice_wake_recording_dir: Path | None = Field(default=None, alias="VOICE_WAKE_RECORDING_DIR")
    voice_wake_recording_retention_days: int = Field(default=7, alias="VOICE_WAKE_RECORDING_RETENTION_DAYS", ge=1)
    voice_wake_recording_preroll_ms: int = Field(default=2000, alias="VOICE_WAKE_RECORDING_PREROLL_MS", ge=0)
    voice_stt_provider: Literal["deterministic", "openai", "faster_whisper"] = Field(
        default="deterministic",
        alias="VOICE_STT_PROVIDER",
    )
    voice_stt_model: str = Field(default="gpt-4o-mini-transcribe", alias="VOICE_STT_MODEL")
    voice_stt_base_url: str = Field(default="https://api.openai.com/v1", alias="VOICE_STT_BASE_URL")
    voice_stt_prompt: str | None = Field(default=None, alias="VOICE_STT_PROMPT")
    voice_stt_timeout_s: float = Field(default=30.0, alias="VOICE_STT_TIMEOUT_S", gt=0)
    voice_stt_preload: bool = Field(default=True, alias="VOICE_STT_PRELOAD")
    voice_stt_faster_whisper_model: str = Field(default="base.en", alias="VOICE_STT_FASTER_WHISPER_MODEL")
    voice_stt_faster_whisper_device: str = Field(default="cpu", alias="VOICE_STT_FASTER_WHISPER_DEVICE")
    voice_stt_faster_whisper_compute_type: str = Field(
        default="int8",
        alias="VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE",
    )
    voice_stt_faster_whisper_temp_dir: Path | None = Field(
        default=None,
        alias="VOICE_STT_FASTER_WHISPER_TEMP_DIR",
    )
    voice_assistant_provider: Literal["local_echo", "ai_node"] = Field(
        default="local_echo",
        alias="VOICE_ASSISTANT_PROVIDER",
    )
    voice_assistant_ai_node_base_url: str | None = Field(default=None, alias="VOICE_ASSISTANT_AI_NODE_BASE_URL")
    voice_assistant_ai_node_turn_path: str = Field(
        default="/api/assistant/turn",
        alias="VOICE_ASSISTANT_AI_NODE_TURN_PATH",
    )
    voice_assistant_timeout_s: float = Field(default=20.0, alias="VOICE_ASSISTANT_TIMEOUT_S", gt=0)
    voice_conversation_context_turns: int = Field(default=6, alias="VOICE_CONVERSATION_CONTEXT_TURNS", ge=0)
    voice_domain_events_enabled: bool = Field(default=True, alias="VOICE_DOMAIN_EVENTS_ENABLED")
    voice_domain_events_mqtt_timeout_s: float = Field(default=5.0, alias="VOICE_DOMAIN_EVENTS_MQTT_TIMEOUT_S", gt=0)
    voice_timer_announcements_enabled: bool = Field(default=True, alias="VOICE_TIMER_ANNOUNCEMENTS_ENABLED")
    voice_timer_success_mqtt_topic: str = Field(
        default="hexe/events/timer/create_succeeded",
        alias="VOICE_TIMER_SUCCESS_MQTT_TOPIC",
    )
    voice_tts_provider: Literal["deterministic", "openai", "piper"] = Field(
        default="deterministic",
        alias="VOICE_TTS_PROVIDER",
    )
    voice_tts_model: str = Field(default="gpt-4o-mini-tts", alias="VOICE_TTS_MODEL")
    voice_tts_voice: str = Field(default="alloy", alias="VOICE_TTS_VOICE")
    voice_tts_base_url: str = Field(default="https://api.openai.com/v1", alias="VOICE_TTS_BASE_URL")
    voice_tts_response_format: str = Field(default="wav", alias="VOICE_TTS_RESPONSE_FORMAT")
    voice_tts_output_sample_rate_hz: int = Field(default=16000, alias="VOICE_TTS_OUTPUT_SAMPLE_RATE_HZ", ge=0)
    voice_tts_timeout_s: float = Field(default=30.0, alias="VOICE_TTS_TIMEOUT_S", gt=0)
    voice_tts_piper_base_url: str | None = Field(default=None, alias="VOICE_TTS_PIPER_BASE_URL")
    voice_tts_piper_service_host: str = Field(default="127.0.0.1", alias="VOICE_TTS_PIPER_SERVICE_HOST")
    voice_tts_piper_service_port: int = Field(default=10200, alias="VOICE_TTS_PIPER_SERVICE_PORT")
    voice_tts_piper_synthesize_path: str = Field(default="/api/tts", alias="VOICE_TTS_PIPER_SYNTHESIZE_PATH")
    voice_tts_piper_voice: str | None = Field(default=None, alias="VOICE_TTS_PIPER_VOICE")
    voice_tts_endpoint_voices: str = Field(default="", alias="VOICE_TTS_ENDPOINT_VOICES")
    piper_tts_model_dir: Path | None = Field(default=None, alias="PIPER_TTS_MODEL_DIR")
    piper_tts_warm_voices: str = Field(default="", alias="PIPER_TTS_WARM_VOICES")
    piper_tts_service_id: str = Field(default="piper_tts", alias="PIPER_TTS_SERVICE_ID")
    piper_tts_container_name: str = Field(default="hexevoice-piper-tts", alias="PIPER_TTS_CONTAINER_NAME")
    piper_tts_control_script: Path = Field(
        default=Path("scripts/piper-tts-control.sh"),
        alias="PIPER_TTS_CONTROL_SCRIPT",
    )
    openwakeword_service_id: str = Field(default="openwakeword", alias="OPENWAKEWORD_SERVICE_ID")
    openwakeword_container_name: str = Field(
        default="hexevoice-openwakeword",
        alias="OPENWAKEWORD_CONTAINER_NAME",
    )
    openwakeword_control_script: Path = Field(
        default=Path("scripts/openwakeword-control.sh"),
        alias="OPENWAKEWORD_CONTROL_SCRIPT",
    )

    def resolved_onboarding_state_path(self) -> Path:
        if self.onboarding_state_path is not None:
            return self.onboarding_state_path
        return self.runtime_dir / "onboarding_state.json"

    def resolved_endpoint_registry_path(self) -> Path:
        if self.endpoint_registry_path is not None:
            return self.endpoint_registry_path
        if self.onboarding_state_path is not None:
            return self.onboarding_state_path.parent / "endpoint_registry.json"
        return self.runtime_dir / "endpoint_registry.json"

    def resolved_voice_intent_registry_path(self) -> Path:
        if self.voice_intent_registry_path is not None:
            return self.voice_intent_registry_path
        if self.onboarding_state_path is not None:
            return self.onboarding_state_path.parent / "voice_intents.json"
        return self.runtime_dir / "voice_intents.json"

    def resolved_endpoint_media_dir(self) -> Path:
        if self.endpoint_media_dir is not None:
            return self.endpoint_media_dir
        return self.runtime_dir / "endpoint_media"

    def resolved_firmware_artifact_dir(self) -> Path:
        if self.firmware_artifact_dir is not None:
            return self.firmware_artifact_dir
        return self.runtime_dir / "firmware"

    def resolved_backend_log_path(self) -> Path:
        if self.backend_log_path is not None:
            return self.backend_log_path
        return self.runtime_dir / "logs" / "hexevoice-backend.log"

    def resolved_voice_record_log_path(self) -> Path:
        if self.voice_record_log_path is not None:
            return self.voice_record_log_path
        return self.runtime_dir / "logs" / "hexevoice-voice-records.log"

    def resolved_faster_whisper_temp_dir(self) -> Path:
        if self.voice_stt_faster_whisper_temp_dir is not None:
            return self.voice_stt_faster_whisper_temp_dir
        return self.runtime_dir / "stt" / "faster-whisper"

    def resolved_voice_wake_recording_dir(self) -> Path:
        if self.voice_wake_recording_dir is not None:
            return self.voice_wake_recording_dir
        return self.runtime_dir / "wake_recordings"

    def resolved_voice_tts_piper_base_url(self) -> str | None:
        if self.voice_tts_piper_base_url is not None:
            return self.voice_tts_piper_base_url.rstrip("/")
        if self.voice_tts_provider != "piper":
            return None
        return f"http://{self.voice_tts_piper_service_host}:{self.voice_tts_piper_service_port}"

    def resolved_piper_tts_model_dir(self) -> Path:
        if self.piper_tts_model_dir is not None:
            return self.piper_tts_model_dir
        return self.runtime_dir / "piper-tts" / "models"

    def resolved_piper_tts_warm_voices(self) -> list[str]:
        return [voice.strip() for voice in self.piper_tts_warm_voices.split(",") if voice.strip()]

    def resolved_voice_tts_endpoint_voices(self) -> dict[str, str]:
        raw = self.voice_tts_endpoint_voices.strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if not isinstance(payload, dict):
                return {}
            return {
                str(endpoint_id).strip(): str(voice).strip()
                for endpoint_id, voice in payload.items()
                if str(endpoint_id).strip() and str(voice).strip()
            }

        endpoint_voices: dict[str, str] = {}
        for entry in raw.split(","):
            if "=" in entry:
                endpoint_id, voice = entry.split("=", 1)
            elif ":" in entry:
                endpoint_id, voice = entry.split(":", 1)
            else:
                continue
            endpoint_id = endpoint_id.strip()
            voice = voice.strip()
            if endpoint_id and voice:
                endpoint_voices[endpoint_id] = voice
        return endpoint_voices
