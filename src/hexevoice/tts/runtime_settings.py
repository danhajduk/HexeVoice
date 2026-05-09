from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from hexevoice.config.settings import Settings
from hexevoice.config.settings import parse_tts_conversion_sample_rates


ALLOWED_TTS_CONVERSION_SAMPLE_RATES = (48000, 22050, 16000)
ALLOWED_TTS_CONVERSION_POLICIES = ("blocking_all", "endpoint_required_sync")


class TtsRuntimeSettingsService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.resolved_voice_tts_runtime_config_path()
        self._model_dir = settings.resolved_piper_tts_model_dir()
        self._piper_env_path = settings.piper_tts_env_path

    def status(self) -> dict[str, Any]:
        config = self._load_config()
        warm_voices = normalized_voice_list(config.get("warm_voices"))
        if not warm_voices:
            warm_voices = self._settings.resolved_piper_tts_warm_voices()
        conversion_rates = sorted(
            parse_tts_conversion_sample_rates(config.get("conversion_sample_rates_hz") or self._settings.voice_tts_conversion_sample_rates).values(),
            reverse=True,
        )
        return {
            "provider": self._settings.voice_tts_provider,
            "config_path": str(self._path),
            "model_dir": str(self._model_dir),
            "models": self.discover_piper_models(),
            "warm_voices": warm_voices,
            "conversion_sample_rates_hz": conversion_rates,
            "allowed_conversion_sample_rates_hz": list(ALLOWED_TTS_CONVERSION_SAMPLE_RATES),
            "conversion_policy": normalize_conversion_policy(
                config.get("conversion_policy") or self._settings.voice_tts_conversion_policy
            ),
            "allowed_conversion_policies": list(ALLOWED_TTS_CONVERSION_POLICIES),
            "restart_required": bool(config.get("restart_required")),
            "updated_at": config.get("updated_at"),
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        model_ids = {model["model_id"] for model in self.discover_piper_models()}
        warm_voices = [
            voice
            for voice in normalized_voice_list(payload.get("warm_voices"))
            if voice in model_ids
        ]
        conversion_rates = sorted(
            parse_tts_conversion_sample_rates(payload.get("conversion_sample_rates_hz")).values(),
            reverse=True,
        )
        config = {
            "warm_voices": warm_voices,
            "conversion_sample_rates_hz": conversion_rates,
            "conversion_policy": normalize_conversion_policy(payload.get("conversion_policy")),
            "restart_required": True,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        self._write_piper_env_warm_voices(warm_voices)
        return self.status()

    def discover_piper_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        if not self._model_dir.exists():
            return models
        for model_path in sorted(self._model_dir.glob("*.onnx")):
            config = read_piper_model_config(model_path)
            audio = config.get("audio") if isinstance(config.get("audio"), dict) else {}
            sample_rate = parse_positive_int(audio.get("sample_rate"))
            models.append(
                {
                    "model_id": model_path.stem,
                    "path": str(model_path),
                    "config_path": str(model_path.with_suffix(model_path.suffix + ".json")),
                    "raw_sample_rate_hz": sample_rate,
                    "quality": audio.get("quality"),
                    "language": config.get("language", {}).get("code") if isinstance(config.get("language"), dict) else None,
                }
            )
        return models

    def _load_config(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_piper_env_warm_voices(self, warm_voices: list[str]) -> None:
        env_path = self._piper_env_path
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        else:
            lines = []
        value = f"PIPER_TTS_WARM_VOICES={','.join(warm_voices)}"
        replaced = False
        updated_lines: list[str] = []
        for line in lines:
            if line.startswith("PIPER_TTS_WARM_VOICES="):
                updated_lines.append(value)
                replaced = True
            else:
                updated_lines.append(line)
        if not replaced:
            updated_lines.append(value)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def normalized_voice_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    voices: list[str] = []
    for value in values:
        voice = str(value or "").strip()
        if voice and voice not in voices:
            voices.append(voice)
    return voices


def read_piper_model_config(model_path: Path) -> dict[str, Any]:
    config_path = model_path.with_suffix(model_path.suffix + ".json")
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_conversion_policy(raw: object) -> str:
    policy = str(raw or "").strip().lower()
    return policy if policy in ALLOWED_TTS_CONVERSION_POLICIES else "blocking_all"


def parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
