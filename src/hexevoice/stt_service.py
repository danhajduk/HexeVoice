from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

from hexevoice.config.settings import Settings
from hexevoice.voice.pipeline import FasterWhisperSpeechToTextAdapter, VoiceTurnAudioSummary


log = logging.getLogger("hexevoice.stt_service")


class TranscribeRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    chunk_count: int = Field(ge=0)
    sample_rate_hz: int | None = None
    encoding: str | None = None
    channels: int = Field(default=1, ge=1)
    audio_base64: str = Field(min_length=1)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    adapter = FasterWhisperSpeechToTextAdapter(
        model_name=app_settings.voice_stt_faster_whisper_model,
        device=app_settings.voice_stt_faster_whisper_device,
        compute_type=app_settings.voice_stt_faster_whisper_compute_type,
        temp_dir=app_settings.resolved_faster_whisper_temp_dir(),
    )
    app = FastAPI(title="HexeVoice STT")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        status = adapter.status()
        return {
            **status,
            "provider": "external_faster_whisper",
            "service": "hexevoice-stt",
        }

    @app.post("/preload")
    async def preload() -> dict[str, Any]:
        result = adapter.preload()
        return {
            **result,
            "provider": "external_faster_whisper",
            "service": "hexevoice-stt",
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
            "error": transcript.error,
        }

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.voice_stt_service_host,
        port=settings.voice_stt_service_port,
    )


if __name__ == "__main__":
    main()
