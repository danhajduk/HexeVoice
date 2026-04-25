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
    onboarding_state_path: Path | None = Field(default=None, alias="ONBOARDING_STATE_PATH")
    bootstrap_mqtt_port: int = Field(default=1884, alias="BOOTSTRAP_MQTT_PORT")
    bootstrap_topic: str = Field(default="hexe/bootstrap/core", alias="BOOTSTRAP_TOPIC")
    provider_id: str = Field(default="voice", alias="PROVIDER_ID")
    voice_wake_provider: Literal["openwakeword", "deterministic"] = Field(
        default="openwakeword",
        alias="VOICE_WAKE_PROVIDER",
    )
    voice_wake_threshold: float = Field(default=0.5, alias="VOICE_WAKE_THRESHOLD", ge=0.0, le=1.0)
    voice_wake_models: str | None = Field(default=None, alias="VOICE_WAKE_MODELS")
    voice_wake_auto_download_models: bool = Field(default=False, alias="VOICE_WAKE_AUTO_DOWNLOAD_MODELS")
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

    def resolved_onboarding_state_path(self) -> Path:
        if self.onboarding_state_path is not None:
            return self.onboarding_state_path
        return self.runtime_dir / "onboarding_state.json"
