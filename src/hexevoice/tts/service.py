from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import wave

from hexevoice.api.models import TtsSynthesizeRequest, TtsSynthesizeResponse
from hexevoice.config.settings import Settings
from hexevoice.voice.pipeline import VoiceTurnPipeline


class TtsAudioService:
    def __init__(self, *, settings: Settings, voice_turn_pipeline: VoiceTurnPipeline) -> None:
        self._settings = settings
        self._pipeline = voice_turn_pipeline
        self._audio_dir = settings.runtime_dir / "voice_tts"

    def synthesize(self, request: TtsSynthesizeRequest) -> TtsSynthesizeResponse:
        self.cleanup_expired()
        session_id = f"tts-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        endpoint_id = request.target.device_id or request.target.location or "tts-client"
        synthesis = self._pipeline.synthesize_reply(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=request.text,
            voice=request.voice,
            audio_format=request.format,
        )
        if synthesis.error:
            return TtsSynthesizeResponse(
                status="failed",
                stream_id=synthesis.stream_id,
                provider_id=synthesis.provider_id,
                error=synthesis.error,
            )

        stream_id = synthesis.stream_id or session_id
        audio_path = self.audio_path(stream_id)
        if audio_path is None:
            audio_path = self._write_deterministic_wav(stream_id)

        content_type = synthesis.content_type or content_type_for_path(audio_path)
        duration_ms = wav_duration_ms(audio_path) if content_type == "audio/wav" else None
        expires_at = datetime.now(UTC) + timedelta(seconds=request.ttl_seconds)
        metadata = {
            "stream_id": stream_id,
            "content_type": content_type,
            "duration_ms": duration_ms,
            "expires_at": expires_at.isoformat(),
            "provider_id": synthesis.provider_id,
            "text_chars": len(request.text or ""),
            "target": request.target.model_dump(mode="json"),
            "requested_format": request.format,
        }
        self._metadata_path(stream_id).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return TtsSynthesizeResponse(
            status="ready",
            audio_url=f"{self.public_api_base_url()}/api/tts/audio/{stream_id}",
            content_type=content_type,
            duration_ms=duration_ms,
            expires_at=expires_at.isoformat(),
            stream_id=stream_id,
            provider_id=synthesis.provider_id,
        )

    def audio_path(self, stream_id: str) -> Path | None:
        safe_stream_id = safe_tts_stream_id(stream_id)
        if safe_stream_id is None:
            return None
        self.cleanup_expired()
        candidates = sorted(self._audio_dir.glob(f"{safe_stream_id}.*"))
        for candidate in candidates:
            if candidate.suffix != ".json" and candidate.is_file():
                return candidate
        return None

    def metadata(self, stream_id: str) -> dict | None:
        safe_stream_id = safe_tts_stream_id(stream_id)
        if safe_stream_id is None:
            return None
        path = self._metadata_path(safe_stream_id)
        if not path.exists():
            return None
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return metadata if isinstance(metadata, dict) else None

    def content_type(self, stream_id: str, path: Path) -> str:
        metadata = self.metadata(stream_id) or {}
        content_type = metadata.get("content_type")
        return str(content_type) if content_type else content_type_for_path(path)

    def cleanup_expired(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        if not self._audio_dir.exists():
            return
        for metadata_path in self._audio_dir.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(str(metadata.get("expires_at")))
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > current:
                continue
            stream_id = metadata_path.stem
            for candidate in self._audio_dir.glob(f"{stream_id}.*"):
                try:
                    candidate.unlink()
                except OSError:
                    pass

    def public_api_base_url(self) -> str:
        base_url = self._settings.public_api_base_url or f"http://{self._settings.api_host}:{self._settings.api_port}"
        return base_url.rstrip("/")

    def _metadata_path(self, stream_id: str) -> Path:
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        return self._audio_dir / f"{stream_id}.json"

    def _write_deterministic_wav(self, stream_id: str) -> Path:
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._audio_dir / f"{stream_id}.wav"
        sample_rate = 16000
        duration_ms = 250
        frames = b"\x00\x00" * int(sample_rate * duration_ms / 1000)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)
        return output_path


def safe_tts_stream_id(stream_id: str) -> str | None:
    cleaned = str(stream_id or "").strip()
    if not cleaned or not cleaned.replace("-", "").replace("_", "").isalnum():
        return None
    return cleaned


def content_type_for_path(path: Path) -> str:
    if path.suffix == ".mp3":
        return "audio/mpeg"
    if path.suffix == ".ogg":
        return "audio/ogg"
    return "audio/wav"


def wav_duration_ms(path: Path) -> int | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getframerate() <= 0:
                return None
            return round((wav_file.getnframes() / wav_file.getframerate()) * 1000)
    except (OSError, wave.Error):
        return None
