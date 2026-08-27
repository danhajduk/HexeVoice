from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field
import uvicorn

from hexevoice.config.settings import Settings
from hexevoice.speaker_id.adapters import SpeakerEmbedding
from hexevoice.speaker_id.adapters import SpeakerIdProviderUnavailable
from hexevoice.speaker_id.adapters import SpeakerThresholds
from hexevoice.speaker_id.adapters import create_speaker_id_adapter


log = logging.getLogger("hexevoice.speaker_id.service")


class AudioSampleRequest(BaseModel):
    sample_id: str | None = None
    audio_base64: str = Field(min_length=1)
    sample_rate_hz: int | None = None
    encoding: str | None = None


class SpeakerProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    speaker_public_id: str | None = Field(default=None, max_length=128)
    labels: list[str] = Field(default_factory=list)


class SpeakerConsentRequest(BaseModel):
    consent_id: str = Field(min_length=1, max_length=160)
    consent_version: str = Field(min_length=1, max_length=80)
    consented_at: str | None = None
    consented_by: str | None = None
    retention_policy: str = "embeddings_only"


class SpeakerEnrollRequest(BaseModel):
    schema_version: int = 1
    request_id: str | None = None
    profile: SpeakerProfileRequest
    consent: SpeakerConsentRequest
    samples: list[AudioSampleRequest] = Field(min_length=1)


class SpeakerIdentifyRequest(BaseModel):
    schema_version: int = 1
    request_id: str | None = None
    audio: AudioSampleRequest
    thresholds: dict[str, float] | None = None


class SpeakerVerifyRequest(BaseModel):
    schema_version: int = 1
    request_id: str | None = None
    speaker_public_id: str | None = None
    profile_id: str | None = None
    audio: AudioSampleRequest
    thresholds: dict[str, float] | None = None


class SpeakerIdConfigRequest(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    identify_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    identify_min_margin: float | None = Field(default=None, ge=0.0, le=1.0)
    verify_min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class SpeakerProfileStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema_version": 1, "profiles": []}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "profiles": []}
        if not isinstance(payload, dict):
            return {"schema_version": 1, "profiles": []}
        profiles = payload.get("profiles")
        if not isinstance(profiles, list):
            payload["profiles"] = []
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def profiles(self) -> list[dict[str, Any]]:
        return [dict(profile) for profile in self.load().get("profiles", []) if isinstance(profile, dict)]

    def upsert(self, profile: dict[str, Any]) -> dict[str, Any]:
        payload = self.load()
        profiles = [dict(item) for item in payload.get("profiles", []) if isinstance(item, dict)]
        profile_id = profile["profile_id"]
        replaced = False
        for index, existing in enumerate(profiles):
            if existing.get("profile_id") == profile_id:
                profiles[index] = profile
                replaced = True
                break
        if not replaced:
            profiles.append(profile)
        payload["profiles"] = profiles
        self.save(payload)
        return profile

    def delete(self, profile_id: str) -> bool:
        payload = self.load()
        profiles = [dict(item) for item in payload.get("profiles", []) if isinstance(item, dict)]
        next_profiles = [profile for profile in profiles if profile.get("profile_id") != profile_id]
        if len(next_profiles) == len(profiles):
            return False
        payload["profiles"] = next_profiles
        self.save(payload)
        return True


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    store = SpeakerProfileStore(app_settings.resolved_voice_speaker_id_profiles_path())
    thresholds = SpeakerThresholds(
        identify_min_confidence=app_settings.voice_speaker_id_identify_min_confidence,
        identify_min_margin=app_settings.voice_speaker_id_identify_min_margin,
        verify_min_score=app_settings.voice_speaker_id_verify_min_score,
    )
    adapter = create_speaker_id_adapter(
        app_settings.voice_speaker_id_provider,
        cache_dir=app_settings.resolved_voice_speaker_id_model_cache_dir(),
        device=app_settings.voice_speaker_id_device,
    )
    enabled = app_settings.voice_speaker_id_enabled
    last_error: str | None = None
    recent_identification_outcomes: list[dict[str, Any]] = []

    def service_status() -> dict[str, Any]:
        status = adapter.status()
        active_transport = "unix_socket" if app_settings.resolved_voice_speaker_id_socket_path() is not None else "tcp"
        is_deterministic = adapter.metadata.provider_id == "deterministic_signal"
        ready = bool(status.get("loaded")) or adapter.metadata.provider_id == "deterministic_signal"
        healthy = bool(status.get("healthy")) or adapter.metadata.provider_id == "deterministic_signal"
        return {
            "status": "ok",
            "ready": ready,
            "healthy": healthy,
            "configured": bool(status.get("configured")) or is_deterministic,
            "enabled": enabled,
            "version": app_settings.node_software_version,
            "service": "hexevoice-speaker-id",
            "provider": adapter.metadata.provider_id,
            "model_id": adapter.metadata.model_id,
            "model": {
                "model_id": adapter.metadata.model_id,
                "embedding_dimensions": adapter.metadata.embedding_dimensions,
                "sample_rate_hz": adapter.metadata.sample_rate_hz,
                "device": str(status.get("device") or app_settings.voice_speaker_id_device),
                "loaded": bool(status.get("loaded")),
            },
            "thresholds": asdict(thresholds),
            "transport": {
                "mode": active_transport,
                "socket_path": str(app_settings.resolved_voice_speaker_id_socket_path())
                if app_settings.resolved_voice_speaker_id_socket_path() is not None
                else None,
                "base_url": app_settings.resolved_voice_speaker_id_base_url(),
                "http_fallback_enabled": app_settings.voice_speaker_id_base_url is not None,
            },
            "profiles_count": len(store.profiles()),
            "last_error": last_error,
            "provider_status": status,
            "recent_identification_outcomes": list(recent_identification_outcomes),
        }

    def record_identification_outcome(kind: str, request_id: str | None, result: dict[str, Any]) -> None:
        outcome = {
            "kind": kind,
            "request_id": request_id,
            "status": result.get("status"),
            "reason": result.get("reason"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        match = result.get("match")
        if isinstance(match, dict):
            outcome["match"] = {
                key: match.get(key)
                for key in (
                    "profile_id",
                    "speaker_public_id",
                    "display_name",
                    "confidence",
                    "score",
                    "score_margin",
                    "provider",
                    "model_id",
                )
            }
        recent_identification_outcomes.insert(0, outcome)
        del recent_identification_outcomes[12:]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        socket_path = app_settings.resolved_voice_speaker_id_socket_path()
        if socket_path is not None:
            socket_path.parent.mkdir(parents=True, exist_ok=True)
            socket_path.parent.chmod(0o700)
        store._path.parent.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="HexeVoice Speaker ID", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        status = service_status()
        return {
            "status": status["status"],
            "ready": status["ready"],
            "version": status["version"],
            "provider": status["provider"],
            "model_id": status["model_id"],
            "transport": status["transport"]["mode"],
            "socket_path": status["transport"]["socket_path"],
            "profiles_count": status["profiles_count"],
            "last_error": status["last_error"],
        }

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return {"schema_version": 1, **service_status()}

    @app.put("/config")
    async def update_config(payload: SpeakerIdConfigRequest) -> dict[str, Any]:
        nonlocal adapter, thresholds, enabled, last_error
        if payload.enabled is not None:
            enabled = payload.enabled
        if payload.provider:
            adapter = create_speaker_id_adapter(
                payload.provider,
                cache_dir=app_settings.resolved_voice_speaker_id_model_cache_dir(),
                device=app_settings.voice_speaker_id_device,
            )
        thresholds = SpeakerThresholds(
            identify_min_confidence=payload.identify_min_confidence
            if payload.identify_min_confidence is not None
            else thresholds.identify_min_confidence,
            identify_min_margin=payload.identify_min_margin
            if payload.identify_min_margin is not None
            else thresholds.identify_min_margin,
            verify_min_score=payload.verify_min_score
            if payload.verify_min_score is not None
            else thresholds.verify_min_score,
        )
        last_error = None
        return {"config_applied": True, **service_status()}

    @app.post("/enroll")
    async def enroll(payload: SpeakerEnrollRequest) -> dict[str, Any]:
        nonlocal last_error
        try:
            embeddings = [_embedding_from_sample(sample) for sample in payload.samples]
        except Exception as exc:
            last_error = str(exc)
            raise _http_error_for_exception(exc) from exc
        profile = _profile_from_enrollment(payload, embeddings, store.profiles())
        store.upsert(profile)
        last_error = None
        return {
            "schema_version": 1,
            "status": "enrolled",
            "request_id": payload.request_id,
            "profile": _public_profile(profile),
        }

    @app.post("/identify")
    async def identify(payload: SpeakerIdentifyRequest) -> dict[str, Any]:
        nonlocal last_error
        if not enabled:
            result = {"status": "disabled", "match": None, "reason": "service_disabled"}
            record_identification_outcome("identify", payload.request_id, result)
            return {"schema_version": 1, "request_id": payload.request_id, **result}
        try:
            candidate = _embedding_from_sample(payload.audio)
            result = _identify_embedding(candidate, store.profiles(), _thresholds_from_payload(payload.thresholds, thresholds))
        except Exception as exc:
            last_error = str(exc)
            raise _http_error_for_exception(exc) from exc
        last_error = None
        record_identification_outcome("identify", payload.request_id, result)
        return {"schema_version": 1, "request_id": payload.request_id, **result}

    @app.post("/verify")
    async def verify(payload: SpeakerVerifyRequest) -> dict[str, Any]:
        nonlocal last_error
        if not enabled:
            result = {"status": "disabled", "verified": False, "reason": "service_disabled", "match": None}
            record_identification_outcome("verify", payload.request_id, result)
            return {"schema_version": 1, "request_id": payload.request_id, **result}
        try:
            candidate = _embedding_from_sample(payload.audio)
            result = _verify_embedding(candidate, store.profiles(), payload, _thresholds_from_payload(payload.thresholds, thresholds))
        except Exception as exc:
            last_error = str(exc)
            raise _http_error_for_exception(exc) from exc
        last_error = None
        record_identification_outcome("verify", payload.request_id, result)
        return {"schema_version": 1, "request_id": payload.request_id, **result}

    @app.get("/profiles")
    async def profiles() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "profiles": [_public_profile(profile) for profile in store.profiles()],
        }

    @app.get("/profiles/{profile_id}")
    async def profile_detail(profile_id: str) -> dict[str, Any]:
        profile = _find_profile(store.profiles(), profile_id=profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_not_found")
        return {"schema_version": 1, "profile": _public_profile(profile)}

    @app.delete("/profiles/{profile_id}")
    async def delete_profile(profile_id: str) -> dict[str, Any]:
        deleted = store.delete(profile_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="profile_not_found")
        return {"schema_version": 1, "status": "deleted", "profile_id": profile_id}

    def _embedding_from_sample(sample: AudioSampleRequest) -> SpeakerEmbedding:
        try:
            audio_bytes = base64.b64decode(sample.audio_base64.encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("invalid_audio_base64") from exc
        return adapter.extract_embedding(audio_bytes)

    return app


def _profile_from_enrollment(
    payload: SpeakerEnrollRequest,
    embeddings: list[SpeakerEmbedding],
    existing_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    speaker_public_id = _safe_public_id(payload.profile.speaker_public_id or payload.profile.display_name)
    existing = _find_profile(existing_profiles, speaker_public_id=speaker_public_id)
    profile_id = str(existing.get("profile_id")) if existing else _unique_profile_id(speaker_public_id, existing_profiles)
    version = int(existing.get("profile_version") or 0) + 1 if existing else 1
    return {
        "profile_id": profile_id,
        "speaker_public_id": speaker_public_id,
        "display_name": payload.profile.display_name,
        "labels": [str(label).strip() for label in payload.profile.labels if str(label).strip()],
        "consent": payload.consent.model_dump(mode="json"),
        "profile_version": version,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "provider_id": embeddings[0].provider_id,
        "model_id": embeddings[0].model_id,
        "embedding_dimensions": embeddings[0].dimensions,
        "sample_count": len(embeddings),
        "audio_retained": False,
        "embeddings": [_embedding_payload(embedding) for embedding in embeddings],
    }


def _embedding_payload(embedding: SpeakerEmbedding) -> dict[str, Any]:
    return {
        "provider_id": embedding.provider_id,
        "model_id": embedding.model_id,
        "values": list(embedding.values),
        "duration_ms": embedding.duration_ms,
        "sample_rate_hz": embedding.sample_rate_hz,
        "audio_duration_ms": embedding.audio_duration_ms,
        "metadata": embedding.metadata,
    }


def _embedding_from_payload(payload: dict[str, Any]) -> SpeakerEmbedding:
    return SpeakerEmbedding(
        provider_id=str(payload["provider_id"]),
        model_id=str(payload["model_id"]),
        values=tuple(float(value) for value in payload.get("values", [])),
        duration_ms=float(payload.get("duration_ms") or 0.0),
        sample_rate_hz=int(payload.get("sample_rate_hz") or 0),
        audio_duration_ms=int(payload.get("audio_duration_ms") or 0),
        metadata=dict(payload.get("metadata") or {}),
    )


def _identify_embedding(
    candidate: SpeakerEmbedding,
    profiles: list[dict[str, Any]],
    thresholds: SpeakerThresholds,
) -> dict[str, Any]:
    ranked = _rank_profiles(candidate, profiles)
    if not ranked:
        return {"status": "unknown", "match": None, "reason": "no_profiles"}
    best = ranked[0]
    second_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = round(float(best["score"]) - float(second_score), 6)
    accepted = float(best["score"]) >= thresholds.identify_min_confidence and margin >= thresholds.identify_min_margin
    if not accepted:
        reason = "low_confidence" if float(best["score"]) < thresholds.identify_min_confidence else "low_margin"
        return {"status": "unknown", "match": _match_payload(best, margin), "reason": reason, "candidates": ranked[:3]}
    return {"status": "identified", "match": _match_payload(best, margin), "reason": None, "candidates": ranked[:3]}


def _verify_embedding(
    candidate: SpeakerEmbedding,
    profiles: list[dict[str, Any]],
    payload: SpeakerVerifyRequest,
    thresholds: SpeakerThresholds,
) -> dict[str, Any]:
    profile = _find_profile(profiles, profile_id=payload.profile_id, speaker_public_id=payload.speaker_public_id)
    if profile is None:
        return {"status": "unknown", "verified": False, "reason": "profile_not_found", "match": None}
    ranked = _rank_profiles(candidate, [profile])
    if not ranked:
        return {"status": "unknown", "verified": False, "reason": "no_compatible_embeddings", "match": None}
    best = ranked[0]
    verified = float(best["score"]) >= thresholds.verify_min_score
    return {
        "status": "verified" if verified else "rejected",
        "verified": verified,
        "reason": None if verified else "below_verify_threshold",
        "match": _match_payload(best, round(float(best["score"]) - thresholds.verify_min_score, 6)),
    }


def _rank_profiles(candidate: SpeakerEmbedding, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    adapter = create_speaker_id_adapter(candidate.provider_id)
    for profile in profiles:
        if profile.get("provider_id") != candidate.provider_id:
            continue
        scores = []
        for embedding_payload in profile.get("embeddings", []):
            if not isinstance(embedding_payload, dict):
                continue
            try:
                reference = _embedding_from_payload(embedding_payload)
                scores.append(adapter.score_embeddings(reference, candidate).score)
            except Exception:
                continue
        if not scores:
            continue
        ranked.append(
            {
                "profile_id": profile.get("profile_id"),
                "speaker_public_id": profile.get("speaker_public_id"),
                "display_name": profile.get("display_name"),
                "score": round(max(scores), 6),
                "provider_id": candidate.provider_id,
                "model_id": candidate.model_id,
            }
        )
    return sorted(ranked, key=lambda item: float(item["score"]), reverse=True)


def _match_payload(match: dict[str, Any], score_margin: float) -> dict[str, Any]:
    return {
        "profile_id": match.get("profile_id"),
        "speaker_public_id": match.get("speaker_public_id"),
        "display_name": match.get("display_name"),
        "confidence": match.get("score"),
        "score": match.get("score"),
        "score_margin": score_margin,
        "provider": match.get("provider_id"),
        "model_id": match.get("model_id"),
    }


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile.get(key)
        for key in (
            "profile_id",
            "speaker_public_id",
            "display_name",
            "labels",
            "consent",
            "profile_version",
            "created_at",
            "updated_at",
            "provider_id",
            "model_id",
            "embedding_dimensions",
            "sample_count",
            "audio_retained",
        )
    }


def _find_profile(
    profiles: list[dict[str, Any]],
    *,
    profile_id: str | None = None,
    speaker_public_id: str | None = None,
) -> dict[str, Any] | None:
    for profile in profiles:
        if profile_id and profile.get("profile_id") == profile_id:
            return profile
        if speaker_public_id and profile.get("speaker_public_id") == speaker_public_id:
            return profile
    return None


def _safe_public_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        normalized = uuid4().hex[:8]
    return normalized if normalized.startswith("speaker_") else f"speaker_{normalized}"


def _unique_profile_id(speaker_public_id: str, profiles: list[dict[str, Any]]) -> str:
    used = {str(profile.get("profile_id")) for profile in profiles}
    base = speaker_public_id
    if base not in used:
        return base
    for index in range(2, 1000):
        candidate = f"{base}_{index}"
        if candidate not in used:
            return candidate
    return f"{base}_{uuid4().hex[:8]}"


def _thresholds_from_payload(payload: dict[str, float] | None, defaults: SpeakerThresholds) -> SpeakerThresholds:
    payload = payload or {}
    return SpeakerThresholds(
        identify_min_confidence=float(payload.get("identify_min_confidence", defaults.identify_min_confidence)),
        identify_min_margin=float(payload.get("identify_min_margin", defaults.identify_min_margin)),
        verify_min_score=float(payload.get("verify_min_score", defaults.verify_min_score)),
    )


def _http_error_for_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, SpeakerIdProviderUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    socket_path = os.getenv("SPEAKER_ID_SOCKET_PATH") or os.getenv("VOICE_SPEAKER_ID_SOCKET_PATH")
    if socket_path:
        path = Path(socket_path)
    else:
        path = settings.resolved_voice_speaker_id_socket_path()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.unlink(missing_ok=True)
        uvicorn.run(create_app(settings), uds=str(path))
        return
    uvicorn.run(
        create_app(settings),
        host=settings.voice_speaker_id_service_host,
        port=settings.voice_speaker_id_service_port,
    )


if __name__ == "__main__":
    main()
