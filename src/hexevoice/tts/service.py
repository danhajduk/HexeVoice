from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
import wave

from hexevoice.api.models import TtsSynthesizeRequest, TtsSynthesizeResponse
from hexevoice.config.settings import Settings
from hexevoice.voice.records import record_voice_event
from hexevoice.voice.pipeline import DEFAULT_TTS_AUDIO_TTL_SECONDS, VoiceTurnPipeline
from hexevoice.voice.pipeline import tts_audio_url_metadata


GENERATED_AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg")
GENERATED_WAV_VARIANTS = ("48k", "22050", "16k", "raw")


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
            voice=self._resolve_voice_model(request.voice),
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
        audio_path = self.audio_path(stream_id, variant=synthesis.audio_variant)
        served_variant = synthesis.audio_variant if audio_path is not None else None
        if audio_path is None:
            audio_path = self._write_deterministic_wav(stream_id)

        content_type = synthesis.content_type or content_type_for_path(audio_path)
        duration_ms = wav_duration_ms(audio_path) if content_type == "audio/wav" else None
        endpoint_audio_url = self._public_tts_audio_url(stream_id, variant=served_variant)
        audio_urls = self._public_tts_audio_variant_urls(stream_id, synthesis.audio_variants)
        if not audio_urls:
            audio_urls = {"raw": self._public_tts_audio_url(stream_id)}
        audio_url_metadata = tts_audio_url_metadata(audio_urls, endpoint_audio_url=endpoint_audio_url)
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(seconds=request.ttl_seconds)
        metadata = {
            "stream_id": stream_id,
            "content_type": content_type,
            "duration_ms": duration_ms,
            "model_id": synthesis.model_id or "deterministic",
            "voice_id": synthesis.voice_id or request.voice,
            **audio_url_metadata,
            "audio_variant": synthesis.audio_variant,
            "audio_variant_sample_rate_hz": synthesis.audio_variant_sample_rate_hz,
            "audio_variant_source_sample_rate_hz": synthesis.audio_variant_source_sample_rate_hz,
            "audio_variants": synthesis.audio_variants,
            "planned_audio_variants": synthesis.planned_audio_variants,
            "pending_audio_variants": synthesis.pending_audio_variants,
            "conversion_policy": synthesis.conversion_policy,
            "raw_audio_path": synthesis.raw_audio_path,
            "raw_sample_rate_hz": synthesis.raw_sample_rate_hz,
            "output_sample_rate_hz": synthesis.output_sample_rate_hz,
            "variant_sample_rates_hz": synthesis.variant_sample_rates_hz,
            "tts_timing_breakdown_ms": synthesis.timing_breakdown_ms,
            "expires_at": expires_at.isoformat(),
            "provider_id": synthesis.provider_id,
            "text_chars": len(request.text or ""),
            "target": request.target.model_dump(mode="json"),
            "requested_format": request.format,
            "ttl_seconds": request.ttl_seconds,
            "created_at": created_at.isoformat(),
        }
        metadata = self._merge_existing_generated_metadata(stream_id, metadata)
        self._metadata_path(stream_id).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return TtsSynthesizeResponse(
            status="ready",
            audio_url=audio_url_metadata.get("audio_url"),
            endpoint_audio_url=endpoint_audio_url,
            audio_urls=audio_urls,
            content_type=content_type,
            duration_ms=duration_ms,
            expires_at=expires_at.isoformat(),
            stream_id=stream_id,
            provider_id=synthesis.provider_id,
        )

    def synthesize_intent_reply(
        self,
        *,
        event_id: str,
        endpoint_id: str,
        session_id: str,
        text: str,
        audio_options: dict | None = None,
        transcript: dict[str, Any] | None = None,
    ) -> dict:
        options = audio_options or {}
        mode = str(options.get("mode") or "best_effort").strip().lower()
        voice = options.get("voice_id") or options.get("model_id") or options.get("voice") or options.get("model")
        resolved_voice = self._resolve_voice_model(str(voice)) if voice else None
        audio_format = str(options.get("format") or "wav")
        lifetime = intent_reply_audio_lifetime(options)
        ttl_seconds = intent_reply_audio_ttl_seconds(options) if lifetime != "long_lived" else None
        stream_id = safe_tts_stream_id(event_id) or f"voice-intent-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        synthesis = self._pipeline.synthesize_reply(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=text,
            voice=resolved_voice,
            audio_format=audio_format,
            stream_id=stream_id,
        )
        audio_path = self.audio_path(stream_id, variant=synthesis.audio_variant)
        served_variant = synthesis.audio_variant if audio_path is not None else None
        if audio_path is None and not synthesis.error:
            audio_path = self._write_deterministic_wav(stream_id)

        content_type = synthesis.content_type or (content_type_for_path(audio_path) if audio_path else "audio/wav")
        duration_ms = wav_duration_ms(audio_path) if audio_path and content_type == "audio/wav" else None
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        voice_ready = bool(audio_path and not synthesis.error)
        endpoint_audio_url = self._public_tts_audio_url(stream_id, variant=served_variant) if voice_ready else None
        audio_urls = self._public_tts_audio_variant_urls(stream_id, synthesis.audio_variants) if voice_ready else {}
        if voice_ready and not audio_urls:
            audio_urls = {"raw": self._public_tts_audio_url(stream_id)}
        audio_url_metadata = tts_audio_url_metadata(audio_urls, endpoint_audio_url=endpoint_audio_url)
        metadata = {
            "event_id": event_id,
            "stream_id": stream_id,
            "voice_ready": voice_ready,
            "spoken_text": text,
            "transcript": transcript,
            **audio_url_metadata,
            "content_type": content_type if voice_ready else None,
            "duration_ms": duration_ms,
            "audio_variant": synthesis.audio_variant,
            "audio_variant_sample_rate_hz": synthesis.audio_variant_sample_rate_hz,
            "audio_variant_source_sample_rate_hz": synthesis.audio_variant_source_sample_rate_hz,
            "audio_variants": synthesis.audio_variants,
            "planned_audio_variants": synthesis.planned_audio_variants,
            "pending_audio_variants": synthesis.pending_audio_variants,
            "conversion_policy": synthesis.conversion_policy,
            "raw_audio_path": synthesis.raw_audio_path,
            "raw_sample_rate_hz": synthesis.raw_sample_rate_hz,
            "output_sample_rate_hz": synthesis.output_sample_rate_hz,
            "variant_sample_rates_hz": synthesis.variant_sample_rates_hz,
            "tts_timing_breakdown_ms": synthesis.timing_breakdown_ms,
            "provider_id": synthesis.provider_id,
            "model_id": synthesis.model_id or options.get("model_id") or resolved_voice or "deterministic",
            "voice_id": synthesis.voice_id or options.get("voice_id") or options.get("voice") or resolved_voice,
            "mode": mode,
            "lifetime": lifetime,
            "ttl_seconds": ttl_seconds,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "error": synthesis.error,
        }
        if synthesis.error and mode == "required":
            metadata["status"] = "failed"
        else:
            metadata["status"] = "ready" if voice_ready else "unavailable"
        metadata = self._merge_existing_generated_metadata(stream_id, metadata)
        self._metadata_path(stream_id).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return metadata

    def audio_path(self, stream_id: str, *, variant: str | None = None) -> Path | None:
        safe_stream_id = safe_tts_stream_id(stream_id)
        if safe_stream_id is None:
            return None
        self.cleanup_expired()
        safe_variant = safe_tts_audio_variant(variant)
        if safe_variant:
            candidate = self._audio_variant_path(safe_stream_id, safe_variant)
            return candidate if candidate.is_file() else None
        metadata = self.metadata(safe_stream_id) or {}
        metadata_variant = safe_tts_audio_variant(metadata.get("audio_variant"))
        if metadata_variant:
            candidate = self._audio_variant_path(safe_stream_id, metadata_variant)
            if candidate.is_file():
                return candidate
        for preferred_variant in GENERATED_WAV_VARIANTS:
            candidate = self._audio_variant_path(safe_stream_id, preferred_variant)
            if candidate.is_file():
                return candidate
        for suffix in GENERATED_AUDIO_SUFFIXES:
            candidate = self._audio_dir / f"{safe_stream_id}{suffix}"
            if candidate.is_file():
                return candidate
        candidates = sorted(self._audio_dir.glob(f"{safe_stream_id}.*"))
        for candidate in candidates:
            if candidate.suffix != ".json" and ".raw." not in candidate.name and candidate.is_file():
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

    def record_fetch_latency(
        self,
        stream_id: str,
        *,
        variant: str | None,
        latency_ms: float,
        audio_path: Path,
        route: str,
    ) -> None:
        safe_stream_id = safe_tts_stream_id(stream_id)
        if safe_stream_id is None:
            return
        metadata_path = self._metadata_path(safe_stream_id)
        metadata = self.metadata(safe_stream_id) or {}
        timings = metadata.get("tts_timing_breakdown_ms") if isinstance(metadata, dict) else {}
        if not isinstance(timings, dict):
            timings = {}
        timings["last_endpoint_fetch_ms"] = latency_ms
        fetch = metadata.get("endpoint_fetch") if isinstance(metadata, dict) else {}
        if not isinstance(fetch, dict):
            fetch = {}
        try:
            count = int(fetch.get("count") or 0) + 1
        except (TypeError, ValueError):
            count = 1
        now = datetime.now(UTC).isoformat()
        fetch.update(
            {
                "count": count,
                "last_latency_ms": latency_ms,
                "last_variant": safe_tts_audio_variant(variant) or "default",
                "last_path": str(audio_path),
                "last_route": route,
                "last_fetched_at": now,
            }
        )
        if metadata_path.exists() and isinstance(metadata, dict):
            metadata["tts_timing_breakdown_ms"] = timings
            metadata["endpoint_fetch"] = fetch
            try:
                metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
            except OSError:
                pass
        record_voice_event(
            "tts.fetch",
            stream_id=safe_stream_id,
            audio_variant=safe_tts_audio_variant(variant) or "default",
            latency_ms=latency_ms,
            audio_path=str(audio_path),
            route=route,
            fetch_count=count,
        )

    def _merge_existing_generated_metadata(self, stream_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        existing = self.metadata(stream_id) or {}
        if not existing:
            return metadata
        merged = dict(metadata)
        for key in (
            "audio_variants",
            "audio_urls",
            "planned_audio_variants",
            "pending_audio_variants",
            "variant_sample_rates_hz",
            "tts_timing_breakdown_ms",
        ):
            existing_value = existing.get(key)
            if isinstance(existing_value, dict):
                merged_value = merged.get(key) if isinstance(merged.get(key), dict) else {}
                merged[key] = {**merged_value, **existing_value}
        for key in (
            "audio_url_16k",
            "audio_url_22050",
            "audio_url_48k",
            "audio_url_48K",
            "ready_audio_variants",
            "optional_conversion_status",
            "optional_conversion_completed_at",
            "optional_conversion_error",
        ):
            if key in existing:
                merged[key] = existing[key]
        return merged

    def list_artifacts(self, *, limit: int = 50) -> dict[str, Any]:
        self.cleanup_expired()
        limit = max(1, min(int(limit or 50), 200))
        if not self._audio_dir.exists():
            return {"artifacts": [], "count": 0, "limit": limit}
        artifacts: list[dict[str, Any]] = []
        metadata_paths = sorted(
            self._audio_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        for metadata_path in metadata_paths[:limit]:
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            stream_id = str(metadata.get("stream_id") or metadata_path.stem)
            artifacts.append(self._artifact_summary(stream_id=stream_id, metadata=metadata, metadata_path=metadata_path))
        return {"artifacts": artifacts, "count": len(artifacts), "limit": limit}

    def _artifact_summary(self, *, stream_id: str, metadata: dict[str, Any], metadata_path: Path) -> dict[str, Any]:
        audio_files = self._artifact_audio_files(stream_id)
        metadata_audio_urls = metadata.get("audio_urls") if isinstance(metadata.get("audio_urls"), dict) else {}
        playable_urls = dict(metadata_audio_urls)
        for variant in audio_files:
            safe_variant = None if variant == "default" else safe_tts_audio_variant(variant)
            key = safe_variant or "default"
            playable_urls.setdefault(key, self._public_tts_audio_url(stream_id, variant=safe_variant))
        file_sizes = {
            variant: file_metadata.get("bytes")
            for variant, file_metadata in audio_files.items()
            if file_metadata.get("bytes") is not None
        }
        sample_rates = metadata.get("variant_sample_rates_hz") if isinstance(metadata.get("variant_sample_rates_hz"), dict) else {}
        return {
            "stream_id": stream_id,
            "created_at": metadata.get("created_at"),
            "expires_at": metadata.get("expires_at"),
            "lifetime": metadata.get("lifetime"),
            "status": metadata.get("status"),
            "voice_ready": metadata.get("voice_ready", bool(audio_files)),
            "provider_id": metadata.get("provider_id"),
            "model_id": metadata.get("model_id"),
            "voice_id": metadata.get("voice_id"),
            "content_type": metadata.get("content_type"),
            "audio_url": metadata.get("audio_url"),
            "endpoint_audio_url": metadata.get("endpoint_audio_url"),
            "playable_urls": playable_urls,
            "audio_variant": metadata.get("audio_variant"),
            "audio_variants": metadata.get("audio_variants") if isinstance(metadata.get("audio_variants"), dict) else {},
            "planned_audio_variants": metadata.get("planned_audio_variants")
            if isinstance(metadata.get("planned_audio_variants"), dict)
            else {},
            "pending_audio_variants": metadata.get("pending_audio_variants")
            if isinstance(metadata.get("pending_audio_variants"), dict)
            else {},
            "conversion_policy": metadata.get("conversion_policy"),
            "optional_conversion_status": metadata.get("optional_conversion_status"),
            "raw_sample_rate_hz": metadata.get("raw_sample_rate_hz"),
            "output_sample_rate_hz": metadata.get("output_sample_rate_hz"),
            "variant_sample_rates_hz": sample_rates,
            "audio_variant_sample_rate_hz": metadata.get("audio_variant_sample_rate_hz"),
            "audio_variant_source_sample_rate_hz": metadata.get("audio_variant_source_sample_rate_hz"),
            "file_sizes": file_sizes,
            "audio_files": audio_files,
            "metadata_path": str(metadata_path),
            "metadata_bytes": self._file_size(metadata_path),
            "tts_timing_breakdown_ms": metadata.get("tts_timing_breakdown_ms")
            if isinstance(metadata.get("tts_timing_breakdown_ms"), dict)
            else {},
            "endpoint_fetch": metadata.get("endpoint_fetch") if isinstance(metadata.get("endpoint_fetch"), dict) else {},
        }

    def _artifact_audio_files(self, stream_id: str) -> dict[str, dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        for candidate in sorted(self._audio_dir.glob(f"{stream_id}.*")):
            if not candidate.is_file() or candidate.suffix.lower() == ".json":
                continue
            variant = self._audio_candidate_variant(stream_id, candidate)
            files[variant] = {
                "path": str(candidate),
                "bytes": self._file_size(candidate),
                "content_type": content_type_for_path(candidate),
                "audio_url": self._public_tts_audio_url(
                    stream_id,
                    variant=None if variant == "default" else variant,
                ),
            }
        return files

    def _audio_candidate_variant(self, stream_id: str, path: Path) -> str:
        name = path.name
        default_names = {f"{stream_id}{suffix}" for suffix in GENERATED_AUDIO_SUFFIXES}
        if name in default_names:
            return "default"
        for variant in GENERATED_WAV_VARIANTS:
            if name == f"{stream_id}.{variant}.wav":
                return variant
        for suffix in GENERATED_AUDIO_SUFFIXES:
            raw_suffix = f".raw{suffix}"
            if name == f"{stream_id}{raw_suffix}":
                return "raw"
            if name.startswith(f"{stream_id}.") and name.endswith(suffix):
                return name[len(stream_id) + 1 : -len(suffix)]
        return "unknown"

    def _file_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except OSError:
            return None

    def cleanup_expired(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        if not self._audio_dir.exists():
            return
        for metadata_path in self._audio_dir.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if str(metadata.get("lifetime") or "").strip().lower() == "long_lived":
                    continue
                expires_at_value = metadata.get("expires_at")
                if not expires_at_value:
                    continue
                expires_at = datetime.fromisoformat(str(expires_at_value))
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

    def cleanup_orphaned_audio(self, *, now: datetime | None = None, min_age_seconds: int = 600) -> int:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=max(0, min_age_seconds))
        deleted_count = 0
        if not self._audio_dir.exists():
            return deleted_count

        for candidate in sorted(self._audio_dir.iterdir()):
            if not candidate.is_file() or candidate.suffix.lower() not in GENERATED_AUDIO_SUFFIXES:
                continue
            metadata_path = self._metadata_path_for_audio_candidate(candidate)
            if metadata_path is not None and metadata_path.exists():
                continue
            try:
                modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
            except OSError:
                continue
            if modified_at > cutoff:
                continue
            try:
                candidate.unlink()
                deleted_count += 1
            except OSError:
                pass
        return deleted_count

    def public_api_base_url(self) -> str:
        base_url = self._settings.public_api_base_url or f"http://{self._settings.api_host}:{self._settings.api_port}"
        return base_url.rstrip("/")

    def _public_tts_audio_url(self, stream_id: str, *, variant: str | None = None) -> str:
        base_url = f"{self.public_api_base_url()}/api/tts/audio/{stream_id}/"
        safe_variant = safe_tts_audio_variant(variant)
        return f"{base_url}{safe_variant}" if safe_variant else base_url

    def _public_tts_audio_variant_urls(self, stream_id: str, audio_variants: dict[str, str]) -> dict[str, str]:
        base_url = self._public_tts_audio_url(stream_id)
        urls: dict[str, str] = {}
        for variant in audio_variants:
            safe_variant = safe_tts_audio_variant(variant)
            if safe_variant:
                urls[safe_variant] = f"{base_url}{safe_variant}"
        return urls

    def _resolve_voice_model(self, voice: str | None) -> str | None:
        if self._settings.voice_tts_provider != "piper" or not voice:
            return voice
        return resolve_piper_voice_model_id(voice, self._settings.resolved_piper_tts_model_dir())

    def _metadata_path(self, stream_id: str) -> Path:
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        return self._audio_dir / f"{stream_id}.json"

    def _audio_variant_path(self, stream_id: str, variant: str) -> Path:
        return self._audio_dir / f"{stream_id}.{variant}.wav"

    def _metadata_path_for_audio_candidate(self, path: Path) -> Path | None:
        name = path.name
        for variant in GENERATED_WAV_VARIANTS:
            variant_suffix = f".{variant}.wav"
            if name.endswith(variant_suffix):
                return self._audio_dir / f"{name[:-len(variant_suffix)]}.json"
        for suffix in GENERATED_AUDIO_SUFFIXES:
            raw_suffix = f".raw{suffix}"
            if name.endswith(raw_suffix):
                return self._audio_dir / f"{name[:-len(raw_suffix)]}.json"
            if name.endswith(suffix):
                return self._audio_dir / f"{name[:-len(suffix)]}.json"
        return None

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


def safe_tts_audio_variant(variant: object) -> str | None:
    cleaned = str(variant or "").strip().lower()
    return cleaned if cleaned in GENERATED_WAV_VARIANTS else None


def intent_reply_audio_lifetime(options: dict | None) -> str:
    if not isinstance(options, dict):
        return "short_lived"
    lifetime = str(options.get("lifetime") or options.get("retention") or "").strip().lower()
    if lifetime in {"long_lived", "long-lived", "persistent", "permanent"}:
        return "long_lived"
    if options.get("long_lived") is True or options.get("persistent") is True:
        return "long_lived"
    return "short_lived"


def intent_reply_audio_ttl_seconds(options: dict | None) -> int:
    if not isinstance(options, dict):
        return DEFAULT_TTS_AUDIO_TTL_SECONDS
    try:
        ttl_seconds = int(options.get("ttl_seconds") or DEFAULT_TTS_AUDIO_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl_seconds = DEFAULT_TTS_AUDIO_TTL_SECONDS
    return max(5, min(ttl_seconds, 3600))


def resolve_piper_voice_model_id(voice: str, model_dir: Path) -> str:
    requested = Path(str(voice or "").strip()).name
    if not requested:
        return requested
    exact = model_dir / f"{requested}.onnx"
    if exact.exists():
        return exact.stem
    requested_key = requested.casefold()
    for model_path in sorted(model_dir.glob("*.onnx")) if model_dir.exists() else []:
        if model_path.stem.casefold() == requested_key:
            return model_path.stem
    return requested


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
