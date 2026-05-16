from pathlib import Path
import json
from typing import Literal

from pydantic import AliasChoices, Field
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
    core_admin_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CORE_ADMIN_TOKEN", "SYNTHIA_ADMIN_TOKEN"),
    )
    voice_local_ui_mode: Literal["full", "setup_only", "disabled"] = Field(default="full", alias="VOICE_LOCAL_UI_MODE")
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
    voice_micro_vad_chunks_enabled: bool = Field(default=False, alias="VOICE_MICRO_VAD_CHUNKS_ENABLED")
    voice_micro_vad_chunk_dir: Path | None = Field(default=None, alias="VOICE_MICRO_VAD_CHUNK_DIR")
    voice_micro_vad_chunk_retention_days: int = Field(default=1, alias="VOICE_MICRO_VAD_CHUNK_RETENTION_DAYS", ge=1)
    voice_session_history_path: Path | None = Field(default=None, alias="VOICE_SESSION_HISTORY_PATH")
    voice_session_history_limit: int = Field(default=100, alias="VOICE_SESSION_HISTORY_LIMIT", ge=1)
    voice_stt_provider: Literal["deterministic", "openai", "faster_whisper", "external_faster_whisper"] = Field(
        default="deterministic",
        alias="VOICE_STT_PROVIDER",
    )
    voice_stt_model: str = Field(default="gpt-4o-mini-transcribe", alias="VOICE_STT_MODEL")
    voice_stt_base_url: str = Field(default="https://api.openai.com/v1", alias="VOICE_STT_BASE_URL")
    voice_stt_prompt: str | None = Field(default=None, alias="VOICE_STT_PROMPT")
    voice_stt_timeout_s: float = Field(default=30.0, alias="VOICE_STT_TIMEOUT_S", gt=0)
    voice_stt_preload: bool = Field(default=True, alias="VOICE_STT_PRELOAD")
    voice_stt_service_transport: Literal["unix", "tcp"] = Field(default="unix", alias="VOICE_STT_SERVICE_TRANSPORT")
    voice_stt_service_base_url: str | None = Field(default=None, alias="VOICE_STT_SERVICE_BASE_URL")
    voice_stt_service_host: str = Field(default="127.0.0.1", alias="VOICE_STT_SERVICE_HOST")
    voice_stt_service_port: int = Field(default=10300, alias="VOICE_STT_SERVICE_PORT")
    voice_stt_service_socket_path: Path | None = Field(default=None, alias="VOICE_STT_SERVICE_SOCKET")
    voice_stt_service_id: str = Field(default="faster_whisper_stt", alias="VOICE_STT_SERVICE_ID")
    voice_stt_service_name: str = Field(default="hexevoice-stt.service", alias="VOICE_STT_SERVICE_NAME")
    voice_stt_container_name: str = Field(
        default="hexevoice-faster-whisper-stt",
        alias="STT_CONTAINER_NAME",
    )
    voice_stt_control_script: Path = Field(
        default=Path("scripts/faster-whisper-stt-control.sh"),
        alias="VOICE_STT_CONTROL_SCRIPT",
    )
    voice_stt_faster_whisper_model: str = Field(default="base.en", alias="VOICE_STT_FASTER_WHISPER_MODEL")
    voice_stt_faster_whisper_device: str = Field(default="cpu", alias="VOICE_STT_FASTER_WHISPER_DEVICE")
    voice_stt_faster_whisper_compute_type: str = Field(
        default="int8",
        alias="VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE",
    )
    voice_stt_faster_whisper_language: str | None = Field(default="en", alias="VOICE_STT_FASTER_WHISPER_LANGUAGE")
    voice_stt_faster_whisper_beam_size: int | None = Field(
        default=5,
        alias="VOICE_STT_FASTER_WHISPER_BEAM_SIZE",
        ge=1,
    )
    voice_stt_faster_whisper_best_of: int | None = Field(
        default=5,
        alias="VOICE_STT_FASTER_WHISPER_BEST_OF",
        ge=1,
    )
    voice_stt_faster_whisper_without_timestamps: bool = Field(
        default=True,
        alias="VOICE_STT_FASTER_WHISPER_WITHOUT_TIMESTAMPS",
    )
    voice_stt_faster_whisper_word_timestamps: bool = Field(
        default=False,
        alias="VOICE_STT_FASTER_WHISPER_WORD_TIMESTAMPS",
    )
    voice_stt_faster_whisper_max_initial_timestamp: float | None = Field(
        default=1.0,
        alias="VOICE_STT_FASTER_WHISPER_MAX_INITIAL_TIMESTAMP",
        ge=0,
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
    voice_tts_piper_transport: Literal["unix", "tcp"] = Field(default="unix", alias="VOICE_TTS_PIPER_TRANSPORT")
    voice_tts_piper_base_url: str | None = Field(default=None, alias="VOICE_TTS_PIPER_BASE_URL")
    voice_tts_piper_service_host: str = Field(default="127.0.0.1", alias="VOICE_TTS_PIPER_SERVICE_HOST")
    voice_tts_piper_service_port: int = Field(default=10200, alias="VOICE_TTS_PIPER_SERVICE_PORT")
    voice_tts_piper_socket_path: Path | None = Field(default=None, alias="VOICE_TTS_PIPER_SOCKET")
    voice_tts_piper_synthesize_path: str = Field(default="/api/tts", alias="VOICE_TTS_PIPER_SYNTHESIZE_PATH")
    voice_tts_piper_voice: str | None = Field(default=None, alias="VOICE_TTS_PIPER_VOICE")
    voice_tts_endpoint_voices: str = Field(default="", alias="VOICE_TTS_ENDPOINT_VOICES")
    voice_tts_endpoint_sample_rates: str = Field(default="", alias="VOICE_TTS_ENDPOINT_SAMPLE_RATES")
    voice_tts_conversion_sample_rates: str = Field(default="48000,16000", alias="VOICE_TTS_CONVERSION_SAMPLE_RATES")
    voice_tts_conversion_policy: Literal["blocking_all", "endpoint_required_sync"] = Field(
        default="blocking_all",
        alias="VOICE_TTS_CONVERSION_POLICY",
    )
    voice_tts_runtime_config_path: Path | None = Field(default=None, alias="VOICE_TTS_RUNTIME_CONFIG_PATH")
    piper_tts_model_dir: Path | None = Field(default=None, alias="PIPER_TTS_MODEL_DIR")
    piper_tts_warm_voices: str = Field(default="", alias="PIPER_TTS_WARM_VOICES")
    piper_tts_service_id: str = Field(default="piper_tts", alias="PIPER_TTS_SERVICE_ID")
    piper_tts_container_name: str = Field(default="hexevoice-piper-tts", alias="PIPER_TTS_CONTAINER_NAME")
    piper_tts_env_path: Path = Field(default=Path("scripts/piper-tts.env"), alias="PIPER_TTS_ENV_PATH")
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

    def resolved_voice_stt_service_base_url(self) -> str:
        if self.voice_stt_service_base_url is not None:
            return self.voice_stt_service_base_url.rstrip("/")
        if self.voice_stt_service_transport == "unix":
            return "http://hexevoice-stt"
        return f"http://{self.voice_stt_service_host}:{self.voice_stt_service_port}"

    def resolved_voice_stt_service_socket_path(self) -> Path | None:
        if self.voice_stt_service_base_url is not None:
            return None
        if self.voice_stt_service_transport != "unix":
            return None
        if self.voice_stt_service_socket_path is not None:
            return self.voice_stt_service_socket_path
        return self.runtime_dir / "sockets" / "stt.sock"

    def resolved_voice_wake_recording_dir(self) -> Path:
        if self.voice_wake_recording_dir is not None:
            return self.voice_wake_recording_dir
        return self.runtime_dir / "wake_recordings"

    def resolved_voice_micro_vad_chunk_dir(self) -> Path:
        if self.voice_micro_vad_chunk_dir is not None:
            return self.voice_micro_vad_chunk_dir
        return self.runtime_dir / "micro_vad_chunks"

    def resolved_voice_session_history_path(self) -> Path:
        if self.voice_session_history_path is not None:
            return self.voice_session_history_path
        return self.runtime_dir / "voice_session_history.json"

    def resolved_voice_tts_runtime_config_path(self) -> Path:
        if self.voice_tts_runtime_config_path is not None:
            return self.voice_tts_runtime_config_path
        return self.runtime_dir / "voice_tts_settings.json"

    def resolved_voice_tts_piper_base_url(self) -> str | None:
        if self.voice_tts_piper_base_url is not None:
            return self.voice_tts_piper_base_url.rstrip("/")
        if self.voice_tts_provider != "piper":
            return None
        if self.voice_tts_piper_transport == "unix":
            return "http://hexevoice-piper-tts"
        return f"http://{self.voice_tts_piper_service_host}:{self.voice_tts_piper_service_port}"

    def resolved_voice_tts_piper_socket_path(self) -> Path | None:
        if self.voice_tts_piper_base_url is not None:
            return None
        if self.voice_tts_piper_transport != "unix":
            return None
        if self.voice_tts_piper_socket_path is not None:
            return self.voice_tts_piper_socket_path
        return self.runtime_dir / "sockets" / "tts.sock"

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

    def resolved_voice_tts_endpoint_sample_rates(self) -> dict[str, int]:
        raw = self.voice_tts_endpoint_sample_rates.strip()
        if not raw:
            return {}
        raw_values: dict[str, object]
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if not isinstance(payload, dict):
                return {}
            raw_values = payload
        else:
            raw_values = {}
            for entry in raw.split(","):
                if "=" in entry:
                    endpoint_id, sample_rate = entry.split("=", 1)
                elif ":" in entry:
                    endpoint_id, sample_rate = entry.split(":", 1)
                else:
                    continue
                raw_values[endpoint_id] = sample_rate

        endpoint_sample_rates: dict[str, int] = {}
        for endpoint_id, sample_rate in raw_values.items():
            endpoint_id = str(endpoint_id).strip()
            try:
                parsed_sample_rate = int(str(sample_rate).strip())
            except (TypeError, ValueError):
                continue
            if endpoint_id and parsed_sample_rate > 0:
                endpoint_sample_rates[endpoint_id] = parsed_sample_rate
        return endpoint_sample_rates

    def resolved_voice_tts_conversion_sample_rates(self) -> dict[str, int]:
        config_rates = self._voice_tts_runtime_config_sample_rates()
        if config_rates:
            return config_rates
        return parse_tts_conversion_sample_rates(self.voice_tts_conversion_sample_rates)

    def resolved_voice_tts_conversion_policy(self) -> str:
        config = self._voice_tts_runtime_config()
        policy = str(config.get("conversion_policy") or self.voice_tts_conversion_policy).strip().lower()
        if policy in {"blocking_all", "endpoint_required_sync"}:
            return policy
        return "blocking_all"

    def _voice_tts_runtime_config_sample_rates(self) -> dict[str, int]:
        return parse_tts_conversion_sample_rates(self._voice_tts_runtime_config().get("conversion_sample_rates_hz"))

    def _voice_tts_runtime_config(self) -> dict:
        path = self.resolved_voice_tts_runtime_config_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


def parse_tts_conversion_sample_rates(raw: object) -> dict[str, int]:
    allowed = {16000: "16k", 22050: "22050", 48000: "48k"}
    if raw is None:
        values: list[object] = []
    elif isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        values = list(raw)
    else:
        values = []

    sample_rates: dict[str, int] = {}
    for value in values:
        try:
            sample_rate = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        variant = allowed.get(sample_rate)
        if variant:
            sample_rates[variant] = sample_rate
    return sample_rates or {"48k": 48000, "16k": 16000}
