from pathlib import Path
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
    backend_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="BACKEND_LOG_LEVEL",
    )
    backend_log_backup_days: int = Field(default=14, alias="BACKEND_LOG_BACKUP_DAYS", ge=1)
    firmware_artifact_dir: Path | None = Field(default=None, alias="FIRMWARE_ARTIFACT_DIR")
    onboarding_state_path: Path | None = Field(default=None, alias="ONBOARDING_STATE_PATH")
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
    voice_stt_provider: Literal["deterministic", "openai"] = Field(default="deterministic", alias="VOICE_STT_PROVIDER")
    voice_stt_model: str = Field(default="gpt-4o-mini-transcribe", alias="VOICE_STT_MODEL")
    voice_stt_base_url: str = Field(default="https://api.openai.com/v1", alias="VOICE_STT_BASE_URL")
    voice_stt_prompt: str | None = Field(default=None, alias="VOICE_STT_PROMPT")
    voice_stt_timeout_s: float = Field(default=30.0, alias="VOICE_STT_TIMEOUT_S", gt=0)
    voice_tts_provider: Literal["deterministic", "openai"] = Field(default="deterministic", alias="VOICE_TTS_PROVIDER")
    voice_tts_model: str = Field(default="gpt-4o-mini-tts", alias="VOICE_TTS_MODEL")
    voice_tts_voice: str = Field(default="alloy", alias="VOICE_TTS_VOICE")
    voice_tts_base_url: str = Field(default="https://api.openai.com/v1", alias="VOICE_TTS_BASE_URL")
    voice_tts_response_format: str = Field(default="wav", alias="VOICE_TTS_RESPONSE_FORMAT")
    voice_tts_timeout_s: float = Field(default=30.0, alias="VOICE_TTS_TIMEOUT_S", gt=0)
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

    def resolved_firmware_artifact_dir(self) -> Path:
        if self.firmware_artifact_dir is not None:
            return self.firmware_artifact_dir
        return self.runtime_dir / "firmware"

    def resolved_backend_log_path(self) -> Path:
        if self.backend_log_path is not None:
            return self.backend_log_path
        return self.runtime_dir / "logs" / "hexevoice-backend.log"
