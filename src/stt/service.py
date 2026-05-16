from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

from hexevoice.config.settings import Settings
from hexevoice.stt_profiles import resolve_stt_model_profile
from stt.adapters import FasterWhisperSpeechToTextAdapter, VoiceTurnAudioSummary


log = logging.getLogger("stt.service")


class TranscribeRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    chunk_count: int = Field(ge=0)
    sample_rate_hz: int | None = None
    encoding: str | None = None
    channels: int = Field(default=1, ge=1)
    audio_base64: str = Field(min_length=1)


class SttConfigRequest(BaseModel):
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None
    warm_model: bool = False


def _build_adapter(
    app_settings: Settings,
    *,
    model_name: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> FasterWhisperSpeechToTextAdapter:
    profile = resolve_stt_model_profile(app_settings)
    return FasterWhisperSpeechToTextAdapter(
        model_name=model_name or profile.model,
        device=device or profile.device,
        compute_type=compute_type or profile.compute_type,
        temp_dir=app_settings.resolved_faster_whisper_temp_dir(),
        language=profile.language,
        beam_size=profile.beam_size,
        best_of=profile.best_of,
        without_timestamps=profile.without_timestamps,
        word_timestamps=profile.word_timestamps,
        max_initial_timestamp=profile.max_initial_timestamp,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    adapter = _build_adapter(app_settings)
    app = FastAPI(title="HexeVoice STT")

    def service_status() -> dict[str, Any]:
        status = adapter.status()
        profile = resolve_stt_model_profile(app_settings)
        return {
            **status,
            "provider": "external_faster_whisper",
            "service": "hexevoice-stt",
            "active_profile": profile.name,
            "fallback_profile": profile.fallback_profile,
            "stt_profile": profile.as_dict(),
        }

    @app.on_event("startup")
    async def preload_on_startup() -> None:
        if not app_settings.voice_stt_preload:
            return
        log.info(
            "Preloading external faster-whisper STT model: model=%s device=%s compute_type=%s",
            adapter.status().get("model"),
            adapter.status().get("device"),
            adapter.status().get("compute_type"),
        )
        result = await asyncio.to_thread(adapter.preload)
        log.info(
            "External faster-whisper STT preload complete: loaded=%s duration_ms=%s error=%s",
            result.get("loaded"),
            result.get("duration_ms"),
            result.get("error"),
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return service_status()

    @app.post("/preload")
    async def preload() -> dict[str, Any]:
        result = adapter.preload()
        return {
            **result,
            "provider": "external_faster_whisper",
            "service": "hexevoice-stt",
        }

    @app.put("/config")
    async def update_config(payload: SttConfigRequest) -> dict[str, Any]:
        nonlocal adapter
        requested_model = str(payload.model or "").strip()
        requested_device = str(payload.device or "").strip()
        requested_compute_type = str(payload.compute_type or "").strip()
        status = adapter.status()
        current_model = str(status.get("model") or "").strip()
        current_device = str(status.get("device") or "").strip()
        current_compute_type = str(status.get("compute_type") or "").strip()
        next_model = requested_model or current_model
        next_device = requested_device or current_device
        next_compute_type = requested_compute_type or current_compute_type
        if (
            next_model != current_model
            or next_device != current_device
            or next_compute_type != current_compute_type
        ):
            log.info(
                "Switching external faster-whisper STT config: model=%s->%s device=%s->%s compute_type=%s->%s",
                current_model,
                next_model,
                current_device,
                next_device,
                current_compute_type,
                next_compute_type,
            )
            adapter = _build_adapter(
                app_settings,
                model_name=next_model,
                device=next_device,
                compute_type=next_compute_type,
            )
        if payload.warm_model:
            result = await asyncio.to_thread(adapter.preload)
            return {
                **service_status(),
                "config_applied": True,
                "preload": result,
            }
        return {
            **service_status(),
            "config_applied": True,
        }

    @app.post("/transcribe")
    async def transcribe(payload: TranscribeRequest) -> dict[str, Any]:
        try:
            audio_bytes = base64.b64decode(payload.audio_base64.encode("ascii"), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_audio_base64") from exc
        transcript = adapter.transcribe(
            VoiceTurnAudioSummary(
                endpoint_id=payload.endpoint_id,
                session_id=payload.session_id,
                chunk_count=payload.chunk_count,
                sample_rate_hz=payload.sample_rate_hz,
                encoding=payload.encoding,
                channels=payload.channels,
                audio_bytes=audio_bytes,
            )
        )
        return {
            "text": transcript.text,
            "confidence": transcript.confidence,
            "provider_id": "external_faster_whisper",
            "model": transcript.model,
            "duration_ms": transcript.duration_ms,
            "timing_breakdown_ms": transcript.timing_breakdown_ms,
            "error": transcript.error,
        }

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    socket_path = os.getenv("STT_SOCKET_PATH") or os.getenv("VOICE_STT_SERVICE_SOCKET")
    if socket_path:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        uvicorn.run(create_app(settings), uds=str(path))
        return
    uvicorn.run(
        create_app(settings),
        host=settings.voice_stt_service_host,
        port=settings.voice_stt_service_port,
    )


if __name__ == "__main__":
    main()
