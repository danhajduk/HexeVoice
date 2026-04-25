from pathlib import Path

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

    def resolved_onboarding_state_path(self) -> Path:
        if self.onboarding_state_path is not None:
            return self.onboarding_state_path
        return self.runtime_dir / "onboarding_state.json"
