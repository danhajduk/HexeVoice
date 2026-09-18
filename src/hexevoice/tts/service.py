from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any
import wave

from hexevoice.api.models import TtsSynthesizeRequest, TtsSynthesizeResponse
from hexevoice.config.settings import Settings
from hexevoice.voice.records import record_voice_event
from hexevoice.voice.pipeline import DEFAULT_TTS_AUDIO_TTL_SECONDS, VoiceTurnPipeline, normalize_wav_sample_rate
from hexevoice.voice.pipeline import tts_audio_url_metadata


GENERATED_AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg")
GENERATED_WAV_VARIANTS = ("48k", "22050", "16k", "raw")
QUALITY_VARIANTS = {"compact": ("16k", 16000), "standard": ("22050", 22050), "high": ("48k", 48000), "source": ("raw", None)}


class TtsAudioService:
    def __init__(self, *, settings: Settings, voice_turn_pipeline: VoiceTurnPipeline) -> None:
        self._settings = settings
        self._pipeline = voice_turn_pipeline
        self._audio_dir = settings.runtime_dir / "voice_tts"

    def synthesize(self, request: TtsSynthesizeRequest) -> TtsSynthesizeResponse:
        self.cleanup_expired()
        if request.delivery is not None and request.delivery.mode != "ephemeral":
            return self._synthesize_managed(request)
        cache_key = normalized_common_clip_cache_key(request.cache_key)
        if cache_key:
            cached = self.cached_common_clip_response(cache_key=cache_key)
            if cached is not None:
                return cached
        session_id = common_clip_stream_id(cache_key) if cache_key else f"tts-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        endpoint_id = request.target.device_id or request.target.location or "tts-client"
        synthesis = self._pipeline.synthesize_reply(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=request.text,
            voice=self._resolve_voice_model(request.voice),
            audio_format=request.format,
            stream_id=session_id if cache_key else None,
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
        response_variants = self._semantic_variant_metadata(
            synthesis.audio_variants,
            audio_urls,
            fallback_path=audio_path,
            fallback_url=endpoint_audio_url,
        )
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
            "cache_key": cache_key,
            "cache_scope": "common_clip" if cache_key else None,
            "stream_url": endpoint_audio_url or audio_url_metadata.get("audio_url"),
            "stream_urls": dict(audio_urls),
            "delivery_mode": "cached" if cache_key else "ephemeral",
            "transcript": request.text,
            "variants": response_variants,
        }
        metadata = self._merge_existing_generated_metadata(stream_id, metadata)
        self._metadata_path(stream_id).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return TtsSynthesizeResponse(
            status="ready",
            audio_url=audio_url_metadata.get("audio_url"),
            endpoint_audio_url=endpoint_audio_url,
            stream_url=endpoint_audio_url or audio_url_metadata.get("audio_url"),
            audio_urls=audio_urls,
            stream_urls=audio_urls,
            content_type=content_type,
            duration_ms=duration_ms,
            expires_at=expires_at.isoformat(),
            stream_id=stream_id,
            provider_id=synthesis.provider_id,
            cache_key=cache_key,
            cache_hit=False,
            delivery_mode="cached" if cache_key else "ephemeral",
            transcript=request.text,
            voice_id=synthesis.voice_id or request.voice,
            model_id=synthesis.model_id or "deterministic",
            variants=response_variants,
        )

    def _synthesize_managed(self, request: TtsSynthesizeRequest) -> TtsSynthesizeResponse:
        delivery = request.delivery
        assert delivery is not None
        qualities = list(dict.fromkeys(delivery.quality_profiles or ["standard"]))
        asset_key = safe_tts_asset_key(delivery.asset_key)
        if delivery.mode in {"named_asset", "persistent"} and asset_key is None:
            return TtsSynthesizeResponse(status="failed", delivery_mode=delivery.mode, error="asset_key_required")
        fingerprint = managed_tts_fingerprint(request, qualities)
        if asset_key is None:
            asset_key = f"cache/{fingerprint[:32]}"
        alias = self._load_asset_alias(asset_key)
        if alias and delivery.update_policy == "if_missing":
            return self._managed_response(alias, cache_hit=True, changed=False)
        if alias and alias.get("fingerprint") == fingerprint and delivery.update_policy != "always":
            return self._managed_response(alias, cache_hit=True, changed=False)

        revision = str(alias.get("revision")) if alias and alias.get("fingerprint") == fingerprint else ""
        revision_metadata = self._load_revision(revision) if revision else None
        revision_cache_hit = revision_metadata is not None
        if revision_metadata is None or delivery.update_policy == "always":
            staging_id = f"ttsstage-{fingerprint[:24]}"
            if delivery.update_policy == "always":
                staging_id = f"{staging_id}-{datetime.now(UTC).strftime('%H%M%S%f')}"
            endpoint_id = request.target.device_id or request.target.location or "tts-client"
            synthesis = self._pipeline.synthesize_reply(
                endpoint_id=endpoint_id,
                session_id=staging_id,
                text=request.text,
                voice=self._resolve_voice_model(request.voice),
                audio_format=request.format,
                stream_id=staging_id,
            )
            if synthesis.error:
                return TtsSynthesizeResponse(
                    status="failed", delivery_mode=delivery.mode, asset_key=asset_key,
                    provider_id=synthesis.provider_id, error=synthesis.error,
                )
            source_path = self.audio_path(staging_id, variant="raw") or self.audio_path(staging_id)
            if source_path is None:
                source_path = self._write_deterministic_wav(staging_id)
            try:
                variants = self._prepare_managed_variants(staging_id, asset_key, qualities, source_path)
            except (OSError, ValueError, wave.Error) as exc:
                return TtsSynthesizeResponse(
                    status="failed", delivery_mode=delivery.mode, asset_key=asset_key,
                    provider_id=synthesis.provider_id, error=f"variant_generation_failed:{exc}",
                )
            revision = managed_tts_revision(variants)
            now = datetime.now(UTC)
            generated_revision_metadata = {
                "revision": revision,
                "variants": variants,
                "voice_id": synthesis.voice_id or request.voice,
                "model_id": synthesis.model_id or "deterministic",
                "provider_id": synthesis.provider_id,
                "created_at": now.isoformat(),
            }
            revision_metadata = self._load_revision(revision) or generated_revision_metadata
            if not self._revision_path(revision).exists():
                self._atomic_json_write(self._revision_path(revision), revision_metadata)

        persistent = delivery.mode == "persistent" or delivery.retention == "persistent"
        alias_expires_at = (
            None
            if persistent or delivery.retention == "until_replaced"
            else datetime.now(UTC) + timedelta(seconds=request.ttl_seconds)
        )
        alias_metadata = {
            **revision_metadata,
            "fingerprint": fingerprint,
            "asset_key": asset_key,
            "delivery_mode": delivery.mode,
            "transcript": request.text,
            "voice_id": request.voice or revision_metadata.get("voice_id"),
            "model_id": revision_metadata.get("model_id") or request.voice or "deterministic",
            "provider_id": revision_metadata.get("provider_id"),
            "retention": delivery.retention or ("persistent" if delivery.mode == "persistent" else "ttl"),
            "source_version": delivery.source_version,
            "expires_at": alias_expires_at.isoformat() if alias_expires_at else None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._atomic_json_write(self._alias_path(asset_key), alias_metadata)
        return self._managed_response(
            alias_metadata,
            cache_hit=revision_cache_hit and delivery.update_policy != "always",
            changed=True,
        )

    def named_asset_audio_path(self, asset_key: str, quality: str) -> tuple[Path, dict[str, Any]] | None:
        safe_key = safe_tts_asset_key(asset_key)
        if safe_key is None or quality not in QUALITY_VARIANTS:
            return None
        alias = self._load_asset_alias(safe_key)
        variant = alias.get("variants", {}).get(quality) if alias else None
        if not isinstance(variant, dict):
            return None
        path = Path(str(variant.get("path") or ""))
        try:
            path.resolve().relative_to(self._audio_dir.resolve())
        except (OSError, ValueError):
            return None
        return (path, variant) if path.is_file() else None

    def _prepare_managed_variants(self, revision: str, asset_key: str, qualities: list[str], source_path: Path) -> dict[str, dict[str, Any]]:
        source_audio = source_path.read_bytes()
        variants: dict[str, dict[str, Any]] = {}
        for quality in qualities:
            variant_name, sample_rate = QUALITY_VARIANTS[quality]
            if quality == "source":
                path = source_path
            else:
                path = self._audio_variant_path(revision, variant_name)
                path.write_bytes(normalize_wav_sample_rate(source_audio, sample_rate))
            sample_rate_hz, channels = wav_properties(path)
            payload = path.read_bytes()
            variants[quality] = {
                "quality": quality,
                "audio_url": self._public_named_asset_url(asset_key, quality),
                "path": str(path),
                "codec": "wav_pcm",
                "sample_rate_hz": sample_rate_hz,
                "channels": channels,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        return variants

    def _semantic_variant_metadata(
        self,
        audio_variants: dict[str, str],
        audio_urls: dict[str, str],
        *,
        fallback_path: Path,
        fallback_url: str,
    ) -> dict[str, dict[str, Any]]:
        quality_for_variant = {variant: quality for quality, (variant, _) in QUALITY_VARIANTS.items()}
        candidates = dict(audio_variants) or {"raw": str(fallback_path)}
        variants: dict[str, dict[str, Any]] = {}
        for variant_name, path_value in candidates.items():
            quality = quality_for_variant.get(variant_name)
            path = Path(path_value)
            if quality is None or not path.is_file():
                continue
            payload = path.read_bytes()
            sample_rate_hz, channels = wav_properties(path)
            variants[quality] = {
                "quality": quality,
                "audio_url": audio_urls.get(variant_name) or fallback_url,
                "codec": "wav_pcm",
                "sample_rate_hz": sample_rate_hz,
                "channels": channels,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        return variants

    def _managed_response(self, metadata: dict[str, Any], *, cache_hit: bool, changed: bool) -> TtsSynthesizeResponse:
        variants = metadata.get("variants") if isinstance(metadata.get("variants"), dict) else {}
        preferred = next((variants[key] for key in ("standard", "high", "compact", "source") if key in variants), None)
        return TtsSynthesizeResponse(
            status="ready", audio_url=preferred.get("audio_url") if preferred else None,
            endpoint_audio_url=preferred.get("audio_url") if preferred else None,
            stream_url=preferred.get("audio_url") if preferred else None,
            audio_urls={key: value["audio_url"] for key, value in variants.items()},
            stream_urls={key: value["audio_url"] for key, value in variants.items()},
            content_type="audio/wav", expires_at=metadata.get("expires_at"), stream_id=metadata.get("revision"),
            provider_id=metadata.get("provider_id"), cache_hit=cache_hit,
            delivery_mode=metadata.get("delivery_mode", "cached"), asset_key=metadata.get("asset_key"),
            revision=metadata.get("revision"), changed=changed, transcript=metadata.get("transcript"),
            voice_id=metadata.get("voice_id"), model_id=metadata.get("model_id"), variants=variants,
        )

    def _public_named_asset_url(self, asset_key: str, quality: str) -> str:
        return f"{self.public_api_base_url()}/api/tts/assets/{asset_key}/audio/{quality}"

    def _asset_store_dir(self) -> Path:
        return self._settings.runtime_dir / "voice_tts_assets"

    def _alias_path(self, asset_key: str) -> Path:
        return self._asset_store_dir() / "aliases" / f"{asset_key}.json"

    def _revision_path(self, revision: str) -> Path:
        return self._asset_store_dir() / "revisions" / f"{revision}.json"

    def _load_asset_alias(self, asset_key: str) -> dict[str, Any] | None:
        return self._load_json(self._alias_path(asset_key))

    def _load_revision(self, revision: str) -> dict[str, Any] | None:
        return self._load_json(self._revision_path(revision))

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    def synthesize_common_clip(self, request: TtsSynthesizeRequest) -> TtsSynthesizeResponse:
        cache_key = normalized_common_clip_cache_key(request.cache_key) or common_clip_cache_key(
            text=request.text,
            voice=request.voice,
            audio_format=request.format,
        )
        return self.synthesize(request.model_copy(update={"cache_key": cache_key}))

    def cached_common_clip_response(self, *, cache_key: str) -> TtsSynthesizeResponse | None:
        stream_id = common_clip_stream_id(cache_key)
        metadata = self.metadata(stream_id)
        if not metadata or not self._metadata_is_available(metadata):
            return None
        audio_path = self.audio_path(stream_id, variant=metadata.get("audio_variant"))
        if audio_path is None:
            return None
        audio_urls = metadata.get("audio_urls") if isinstance(metadata.get("audio_urls"), dict) else {}
        if not audio_urls:
            audio_urls = self._public_tts_audio_variant_urls(stream_id, metadata.get("audio_variants") if isinstance(metadata.get("audio_variants"), dict) else {})
        endpoint_audio_url = metadata.get("endpoint_audio_url") or metadata.get("audio_url")
        return TtsSynthesizeResponse(
            status="ready",
            audio_url=metadata.get("audio_url") or endpoint_audio_url,
            endpoint_audio_url=endpoint_audio_url,
            stream_url=endpoint_audio_url or metadata.get("audio_url"),
            audio_urls=audio_urls,
            stream_urls=audio_urls,
            content_type=metadata.get("content_type") or content_type_for_path(audio_path),
            duration_ms=metadata.get("duration_ms"),
            expires_at=metadata.get("expires_at"),
            stream_id=stream_id,
            provider_id=metadata.get("provider_id"),
            cache_key=cache_key,
            cache_hit=True,
            delivery_mode=metadata.get("delivery_mode", "cached"),
            changed=False,
            transcript=metadata.get("transcript"),
            voice_id=metadata.get("voice_id"),
            model_id=metadata.get("model_id"),
            variants=metadata.get("variants") if isinstance(metadata.get("variants"), dict) else {},
        )

    def _metadata_is_available(self, metadata: dict[str, Any]) -> bool:
        if str(metadata.get("lifetime") or "").strip().lower() == "long_lived":
            return True
        expires_at_value = metadata.get("expires_at")
        if not expires_at_value:
            return True
        try:
            expires_at = datetime.fromisoformat(str(expires_at_value))
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

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
        if not isinstance(metadata, dict):
            return None
        return self._metadata_with_compat_defaults(safe_stream_id, metadata)

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

    def _metadata_with_compat_defaults(self, stream_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(metadata)
        normalized.setdefault("stream_id", stream_id)
        audio_files = self._artifact_audio_files(stream_id)
        if audio_files and not normalized.get("content_type"):
            first_file = next(iter(audio_files.values()))
            normalized["content_type"] = first_file.get("content_type")
        if audio_files and not isinstance(normalized.get("audio_variants"), dict):
            normalized["audio_variants"] = {
                variant: file_metadata["path"]
                for variant, file_metadata in audio_files.items()
                if variant != "default" and file_metadata.get("path")
            }
        if audio_files and not isinstance(normalized.get("audio_urls"), dict):
            normalized["audio_urls"] = {
                variant: file_metadata["audio_url"]
                for variant, file_metadata in audio_files.items()
                if file_metadata.get("audio_url")
            }
        audio_urls = normalized.get("audio_urls") if isinstance(normalized.get("audio_urls"), dict) else {}
        if not normalized.get("audio_url"):
            default_url = (
                normalized.get("endpoint_audio_url")
                or audio_urls.get(str(normalized.get("audio_variant") or ""))
                or audio_urls.get("default")
                or audio_urls.get("raw")
            )
            if default_url:
                normalized["audio_url"] = default_url
        if not normalized.get("audio_variant") and audio_files:
            for preferred_variant in GENERATED_WAV_VARIANTS + ("default",):
                if preferred_variant in audio_files:
                    normalized["audio_variant"] = preferred_variant
                    break
        if not normalized.get("endpoint_audio_url") and normalized.get("audio_variant"):
            audio_urls = normalized.get("audio_urls") if isinstance(normalized.get("audio_urls"), dict) else {}
            normalized["endpoint_audio_url"] = audio_urls.get(normalized["audio_variant"]) or normalized.get("audio_url")
        if "voice_ready" not in normalized:
            normalized["voice_ready"] = bool(audio_files)
        if not normalized.get("stream_url"):
            normalized["stream_url"] = normalized.get("endpoint_audio_url") or normalized.get("audio_url")
        if not isinstance(normalized.get("stream_urls"), dict):
            normalized["stream_urls"] = dict(audio_urls)
        return normalized

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

    def delete_artifact(self, stream_id: str) -> dict[str, Any]:
        safe_stream_id = safe_tts_stream_id(stream_id)
        if safe_stream_id is None:
            return {"stream_id": stream_id, "deleted_count": 0, "deleted_paths": [], "reason": "invalid_stream_id"}
        deleted: list[str] = []
        if self._audio_dir.exists():
            for candidate in sorted(self._audio_dir.glob(f"{safe_stream_id}.*")):
                if not candidate.is_file():
                    continue
                try:
                    candidate.unlink()
                    deleted.append(str(candidate))
                except OSError:
                    pass
        return {
            "stream_id": safe_stream_id,
            "deleted_count": len(deleted),
            "deleted_paths": deleted,
            "status": "deleted" if deleted else "not_found",
        }

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
            "stream_url": metadata.get("stream_url") or metadata.get("endpoint_audio_url") or metadata.get("audio_url"),
            "playable_urls": playable_urls,
            "stream_urls": metadata.get("stream_urls") if isinstance(metadata.get("stream_urls"), dict) else playable_urls,
            "cache_key": metadata.get("cache_key"),
            "cache_scope": metadata.get("cache_scope"),
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
            record_voice_event(
                "tts.expired",
                stream_id=stream_id,
                expires_at=expires_at.isoformat(),
                cache_key=metadata.get("cache_key"),
            )
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


def safe_tts_asset_key(asset_key: str | None) -> str | None:
    cleaned = str(asset_key or "").strip().strip("/")
    parts = cleaned.split("/")
    if not cleaned or len(cleaned) > 180 or any(
        not part
        or part in {".", ".."}
        or part.startswith(".")
        or len(part) > 64
        or not all(char.isalnum() or char in {"-", "_", "."} for char in part)
        for part in parts
    ):
        return None
    return "/".join(parts)


def managed_tts_fingerprint(request: TtsSynthesizeRequest, qualities: list[str]) -> str:
    delivery = request.delivery
    identity = {
        "text": request.text,
        "voice": request.voice,
        "format": request.format,
        "qualities": sorted(qualities),
        "source_version": delivery.source_version if delivery else None,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def managed_tts_revision(variants: dict[str, dict[str, Any]]) -> str:
    content_identity = {
        quality: {"sha256": variant.get("sha256"), "size_bytes": variant.get("size_bytes")}
        for quality, variant in sorted(variants.items())
    }
    digest = hashlib.sha256(
        json.dumps(content_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"ttsrev-{digest[:32]}"


def wav_properties(path: Path) -> tuple[int | None, int | None]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            return wav_file.getframerate(), wav_file.getnchannels()
    except (OSError, wave.Error):
        return None, None


def normalized_common_clip_cache_key(cache_key: str | None) -> str | None:
    cleaned = str(cache_key or "").strip().lower()
    if not cleaned:
        return None
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in cleaned)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:96] or None


def common_clip_cache_key(*, text: str, voice: str | None, audio_format: str) -> str:
    identity = json.dumps(
        {
            "text": str(text or "").strip(),
            "voice": str(voice or "").strip(),
            "format": str(audio_format or "wav").strip().lower(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def common_clip_stream_id(cache_key: str) -> str:
    safe_key = normalized_common_clip_cache_key(cache_key) or hashlib.sha256(str(cache_key).encode("utf-8")).hexdigest()[:32]
    return f"common-{safe_key}"


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
