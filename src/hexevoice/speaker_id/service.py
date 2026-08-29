from __future__ import annotations

import base64
from io import BytesIO
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
import wave

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
from hexevoice.speaker_id.phrase_sets import ACTIVE_SPEAKER_PHRASE_SET_VERSION
from hexevoice.speaker_id.phrase_sets import active_phrase_set_payload
from hexevoice.speaker_id.phrase_sets import select_holdout_phrases
from hexevoice.voice.audio_quality import analyze_pcm_s16le_audio
from hexevoice.voice.metric_schemas import speaker_confidence_tier


log = logging.getLogger("hexevoice.speaker_id.service")

ENROLLMENT_REQUIRED_SAMPLE_COUNT = 8
ENROLLMENT_RECOMMENDED_SAMPLE_COUNT_MIN = 12
ENROLLMENT_RECOMMENDED_SAMPLE_COUNT_MAX = 16
ENROLLMENT_REQUIRED_TOTAL_DURATION_MS = 8000
ENROLLMENT_TARGET_TOTAL_DURATION_MS = 30000
ENROLLMENT_MIN_SAMPLE_DURATION_MS = 700
ENROLLMENT_FATAL_WARNINGS = {"missing_audio", "unsupported_audio", "short_audio", "silent"}
AGE_BANDS = {"child", "teen", "adult", "unknown"}
AGE_REVIEW_INTERVAL_DAYS = {
    "child": 45,
    "teen": 75,
    "adult": 365,
    "unknown": None,
}
AGE_RESTRICTION_CLASS = {
    "child": "child",
    "teen": "teen",
    "adult": "adult",
    "unknown": "unknown",
}


class AudioSampleRequest(BaseModel):
    sample_id: str | None = None
    audio_base64: str = Field(min_length=1)
    sample_rate_hz: int | None = None
    encoding: str | None = None
    phrase_set_version: str | None = None
    phrase_id: str | None = None
    phrase_text: str | None = None
    phrase_status: str | None = "accepted"


class SpeakerProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    speaker_public_id: str | None = Field(default=None, max_length=128)
    labels: list[str] = Field(default_factory=list)
    age_band: str | None = None
    age_restriction_class: str | None = None
    guardian_managed: bool | None = None
    profile_review_interval_days: int | None = Field(default=None, ge=1)
    last_voice_profile_review_at: str | None = None
    admin_eligible: bool | None = None


class SpeakerConsentRequest(BaseModel):
    consent_id: str = Field(min_length=1, max_length=160)
    consent_version: str = Field(min_length=1, max_length=80)
    consented_at: str | None = None
    consented_by: str | None = None
    retention_policy: str = "embeddings_only"


class SpeakerEnrollRequest(BaseModel):
    schema_version: int = 1
    request_id: str | None = None
    phrase_set_version: str | None = None
    profile: SpeakerProfileRequest
    consent: SpeakerConsentRequest
    samples: list[AudioSampleRequest] = Field(min_length=1)


class SpeakerProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    speaker_public_id: str | None = Field(default=None, max_length=128)
    labels: list[str] | None = None
    age_band: str | None = None
    age_restriction_class: str | None = None
    guardian_managed: bool | None = None
    profile_review_interval_days: int | None = Field(default=None, ge=1)
    last_voice_profile_review_at: str | None = None
    admin_eligible: bool | None = None


class SpeakerProfileSamplesRequest(BaseModel):
    schema_version: int = 1
    request_id: str | None = None
    phrase_set_version: str | None = None
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


def _load_runtime_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_runtime_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    store = SpeakerProfileStore(app_settings.resolved_voice_speaker_id_profiles_path())
    runtime_config_path = app_settings.resolved_voice_speaker_id_runtime_config_path()
    runtime_config = _load_runtime_config(runtime_config_path)
    saved_thresholds = runtime_config.get("thresholds") if isinstance(runtime_config.get("thresholds"), dict) else {}
    thresholds = SpeakerThresholds(
        identify_min_confidence=float(
            saved_thresholds.get("identify_min_confidence", app_settings.voice_speaker_id_identify_min_confidence)
        ),
        identify_min_margin=float(
            saved_thresholds.get("identify_min_margin", app_settings.voice_speaker_id_identify_min_margin)
        ),
        verify_min_score=float(
            saved_thresholds.get("verify_min_score", app_settings.voice_speaker_id_verify_min_score)
        ),
    )
    configured_provider = str(runtime_config.get("provider") or app_settings.voice_speaker_id_provider).strip()
    adapter = create_speaker_id_adapter(
        configured_provider,
        cache_dir=app_settings.resolved_voice_speaker_id_model_cache_dir(),
        device=app_settings.voice_speaker_id_device,
    )
    enabled = bool(runtime_config.get("enabled", app_settings.voice_speaker_id_enabled))
    last_error: str | None = None
    recent_identification_outcomes: list[dict[str, Any]] = []

    def persist_config() -> None:
        _save_runtime_config(
            runtime_config_path,
            {
                "schema_version": 1,
                "updated_at": datetime.now(UTC).isoformat(),
                "enabled": enabled,
                "provider": adapter.metadata.provider_id,
                "thresholds": asdict(thresholds),
            },
        )

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
                    "confidence_tier",
                    "age_band",
                    "age_restriction_class",
                    "admin_eligible",
                    "learning_eligible",
                )
            }
        candidates = result.get("candidates")
        if isinstance(candidates, list):
            outcome["candidates"] = [
                _match_payload(candidate, 0.0)
                for candidate in candidates[:3]
                if isinstance(candidate, dict)
            ]
        if "verified" in result:
            outcome["verified"] = bool(result.get("verified"))
        recent_identification_outcomes.insert(0, outcome)
        del recent_identification_outcomes[12:]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal last_error
        socket_path = app_settings.resolved_voice_speaker_id_socket_path()
        if socket_path is not None:
            socket_path.parent.mkdir(parents=True, exist_ok=True)
            socket_path.parent.chmod(0o700)
        store._path.parent.mkdir(parents=True, exist_ok=True)
        if enabled and app_settings.voice_speaker_id_preload:
            try:
                adapter.warm_up()
                last_error = None
                log.info(
                    "Speaker ID provider preloaded: provider=%s model=%s",
                    adapter.metadata.provider_id,
                    adapter.metadata.model_id,
                )
            except Exception as exc:
                last_error = str(exc)
                log.warning("Speaker ID provider preload failed: %s", exc)
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

    @app.get("/phrase-sets")
    async def phrase_sets() -> dict[str, Any]:
        return active_phrase_set_payload()

    @app.get("/phrase-sets/holdout-selection")
    async def holdout_selection(count: int = 6, seed: str | None = None) -> dict[str, Any]:
        return select_holdout_phrases(count=count, seed=seed)

    @app.put("/config")
    async def update_config(payload: SpeakerIdConfigRequest) -> dict[str, Any]:
        nonlocal adapter, thresholds, enabled, last_error
        if payload.enabled is not None:
            enabled = payload.enabled
        if payload.provider:
            candidate_adapter = create_speaker_id_adapter(
                payload.provider,
                cache_dir=app_settings.resolved_voice_speaker_id_model_cache_dir(),
                device=app_settings.voice_speaker_id_device,
            )
            candidate_status = candidate_adapter.status()
            if candidate_adapter.metadata.provider_id != "deterministic_signal" and not bool(
                candidate_status.get("configured")
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": _provider_not_installed_message(candidate_status),
                        "reason": candidate_status.get("reason") or "provider_not_installed",
                        "provider": candidate_adapter.metadata.provider_id,
                        "install_hint": candidate_status.get("install_hint"),
                        "dependencies": candidate_status.get("dependencies"),
                    },
                )
            adapter = candidate_adapter
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
        persist_config()
        return {"config_applied": True, **service_status()}

    @app.post("/enroll")
    async def enroll(payload: SpeakerEnrollRequest) -> dict[str, Any]:
        nonlocal last_error
        try:
            embedding_records = [_embedding_record_from_enrollment_sample(sample) for sample in payload.samples]
            readiness = _enrollment_readiness(
                embedding_records,
                expected_sample_rate_hz=adapter.metadata.sample_rate_hz,
            )
            if not readiness["can_enroll"]:
                raise ValueError(f"enrollment_not_ready:{','.join(readiness['blocking_reasons'])}")
        except Exception as exc:
            last_error = str(exc)
            raise _http_error_for_exception(exc) from exc
        profile = _profile_from_enrollment(
            payload,
            [record["embedding"] for record in embedding_records],
            store.profiles(),
            readiness=readiness,
            samples=[record["sample"] for record in embedding_records],
        )
        store.upsert(profile)
        last_error = None
        return {
            "schema_version": 1,
            "status": "enrolled",
            "request_id": payload.request_id,
            "enrollment_readiness": readiness,
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

    @app.patch("/profiles/{profile_id}")
    async def update_profile(profile_id: str, payload: SpeakerProfileUpdateRequest) -> dict[str, Any]:
        profiles = store.profiles()
        profile = _find_profile(profiles, profile_id=profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_not_found")
        try:
            updated_profile = _profile_with_metadata_updates(profile, payload, profiles)
        except Exception as exc:
            raise _http_error_for_exception(exc) from exc
        _replace_profile(store, profiles, updated_profile)
        return {"schema_version": 1, "status": "updated", "profile": _public_profile(updated_profile)}

    @app.post("/profiles/{profile_id}/samples")
    async def append_profile_samples(profile_id: str, payload: SpeakerProfileSamplesRequest) -> dict[str, Any]:
        nonlocal last_error
        profiles = store.profiles()
        profile = _find_profile(profiles, profile_id=profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_not_found")
        try:
            new_records = [_embedding_record_from_enrollment_sample(sample) for sample in payload.samples]
            updated_profile = _profile_with_appended_samples(
                profile,
                new_records,
                expected_sample_rate_hz=adapter.metadata.sample_rate_hz,
                phrase_set_version=payload.phrase_set_version or str(profile.get("phrase_set_version") or ACTIVE_SPEAKER_PHRASE_SET_VERSION),
            )
            if updated_profile.get("provider_id") != adapter.metadata.provider_id:
                raise ValueError("profile_provider_mismatch")
        except Exception as exc:
            last_error = str(exc)
            raise _http_error_for_exception(exc) from exc
        _replace_profile(store, profiles, updated_profile)
        last_error = None
        return {
            "schema_version": 1,
            "status": "samples_added",
            "request_id": payload.request_id,
            "profile": _public_profile(updated_profile),
        }

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

    def _embedding_record_from_enrollment_sample(sample: AudioSampleRequest) -> dict[str, Any]:
        try:
            audio_bytes = base64.b64decode(sample.audio_base64.encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("invalid_audio_base64") from exc
        quality = _audio_quality_from_wav_bytes(audio_bytes)
        if quality["quality"]["status"] in ENROLLMENT_FATAL_WARNINGS:
            raise ValueError(f"enrollment_sample_unusable:{quality['sample_id'] or sample.sample_id or 'sample'}:{quality['quality']['status']}")
        try:
            embedding = adapter.extract_embedding(audio_bytes)
        finally:
            del audio_bytes
        return {
            "embedding": embedding,
            "sample": {
                "sample_id": sample.sample_id,
                "phrase_set_version": sample.phrase_set_version or payload_phrase_set_version(sample),
                "phrase_id": sample.phrase_id,
                "phrase_text": sample.phrase_text,
                "phrase_status": sample.phrase_status or "accepted",
                "audio_duration_ms": quality["duration_ms"],
                "sample_rate_hz": quality["sample_rate_hz"],
                "channels": quality["channels"],
                "quality": quality["quality"],
            },
        }

    return app


def _profile_from_enrollment(
    payload: SpeakerEnrollRequest,
    embeddings: list[SpeakerEmbedding],
    existing_profiles: list[dict[str, Any]],
    *,
    readiness: dict[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    speaker_public_id = _safe_public_id(payload.profile.speaker_public_id or payload.profile.display_name)
    phrase_set_version = payload.phrase_set_version or ACTIVE_SPEAKER_PHRASE_SET_VERSION
    age_policy = _profile_age_policy(payload.profile, now_iso=now)
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
        "phrase_set_version": phrase_set_version,
        "phrase_tracking": _phrase_tracking(samples, phrase_set_version=phrase_set_version),
        **age_policy,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "provider_id": embeddings[0].provider_id,
        "model_id": embeddings[0].model_id,
        "embedding_dimensions": embeddings[0].dimensions,
        "sample_count": len(embeddings),
        "accepted_sample_count": readiness["accepted_sample_count"],
        "total_accepted_speech_duration_ms": readiness["total_accepted_speech_duration_ms"],
        "enrollment_readiness": readiness,
        "enrollment_samples": samples,
        "learning_eligible": False,
        "audio_retained": False,
        "embeddings": [_embedding_payload(embedding) for embedding in embeddings],
    }


def _profile_with_metadata_updates(
    profile: dict[str, Any],
    payload: SpeakerProfileUpdateRequest,
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    display_name = payload.display_name if payload.display_name is not None else str(profile.get("display_name") or "")
    speaker_public_id = str(profile.get("speaker_public_id") or "")
    if payload.speaker_public_id is not None:
        speaker_public_id = _safe_public_id(payload.speaker_public_id)
        existing = _find_profile(profiles, speaker_public_id=speaker_public_id)
        if existing is not None and existing.get("profile_id") != profile.get("profile_id"):
            raise ValueError("speaker_public_id_already_exists")
    labels = profile.get("labels") if payload.labels is None else payload.labels
    age_profile = SpeakerProfileRequest(
        display_name=display_name,
        speaker_public_id=speaker_public_id,
        labels=[str(label).strip() for label in labels or [] if str(label).strip()],
        age_band=payload.age_band if payload.age_band is not None else profile.get("age_band"),
        age_restriction_class=payload.age_restriction_class
        if payload.age_restriction_class is not None
        else profile.get("age_restriction_class"),
        guardian_managed=payload.guardian_managed if payload.guardian_managed is not None else profile.get("guardian_managed"),
        profile_review_interval_days=payload.profile_review_interval_days
        if payload.profile_review_interval_days is not None
        else profile.get("profile_review_interval_days"),
        last_voice_profile_review_at=payload.last_voice_profile_review_at
        if payload.last_voice_profile_review_at is not None
        else profile.get("last_voice_profile_review_at"),
        admin_eligible=payload.admin_eligible if payload.admin_eligible is not None else profile.get("admin_eligible"),
    )
    updated = dict(profile)
    updated.update(
        {
            "speaker_public_id": speaker_public_id,
            "display_name": display_name,
            "labels": age_profile.labels,
            "updated_at": now,
            "profile_version": int(profile.get("profile_version") or 0) + 1,
            **_profile_age_policy(age_profile, now_iso=now),
        }
    )
    return updated


def _profile_with_appended_samples(
    profile: dict[str, Any],
    new_records: list[dict[str, Any]],
    *,
    expected_sample_rate_hz: int,
    phrase_set_version: str,
) -> dict[str, Any]:
    existing_records = _embedding_records_from_profile(profile)
    all_records = [*existing_records, *new_records]
    if not all_records:
        raise ValueError("no_profile_samples")
    provider_ids = {record["embedding"].provider_id for record in all_records}
    if len(provider_ids) != 1 or profile.get("provider_id") not in provider_ids:
        raise ValueError("profile_provider_mismatch")
    readiness = _enrollment_readiness(all_records, expected_sample_rate_hz=expected_sample_rate_hz)
    if not readiness["can_enroll"]:
        raise ValueError(f"enrollment_not_ready:{','.join(readiness['blocking_reasons'])}")
    embeddings = [record["embedding"] for record in all_records]
    samples = [record["sample"] for record in all_records]
    updated = dict(profile)
    updated.update(
        {
            "updated_at": datetime.now(UTC).isoformat(),
            "profile_version": int(profile.get("profile_version") or 0) + 1,
            "phrase_set_version": phrase_set_version,
            "phrase_tracking": _phrase_tracking(samples, phrase_set_version=phrase_set_version),
            "provider_id": embeddings[0].provider_id,
            "model_id": embeddings[0].model_id,
            "embedding_dimensions": embeddings[0].dimensions,
            "sample_count": len(embeddings),
            "accepted_sample_count": readiness["accepted_sample_count"],
            "total_accepted_speech_duration_ms": readiness["total_accepted_speech_duration_ms"],
            "enrollment_readiness": readiness,
            "enrollment_samples": samples,
            "learning_eligible": False,
            "audio_retained": False,
            "embeddings": [_embedding_payload(embedding) for embedding in embeddings],
        }
    )
    return updated


def _embedding_records_from_profile(profile: dict[str, Any]) -> list[dict[str, Any]]:
    embeddings = [item for item in profile.get("embeddings") or [] if isinstance(item, dict)]
    samples = [item for item in profile.get("enrollment_samples") or [] if isinstance(item, dict)]
    records = []
    for index, embedding_payload in enumerate(embeddings):
        sample = samples[index] if index < len(samples) else {"sample_id": f"sample-{index + 1}"}
        records.append({"embedding": _embedding_from_payload(embedding_payload), "sample": dict(sample)})
    return records


def _replace_profile(store: SpeakerProfileStore, profiles: list[dict[str, Any]], updated_profile: dict[str, Any]) -> None:
    payload = store.load()
    profile_id = updated_profile.get("profile_id")
    payload["profiles"] = [
        updated_profile if isinstance(profile, dict) and profile.get("profile_id") == profile_id else profile
        for profile in profiles
    ]
    store.save(payload)


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
                "enrollment_readiness": profile.get("enrollment_readiness"),
                "age_band": profile.get("age_band"),
                "age_restriction_class": profile.get("age_restriction_class"),
                "admin_eligible": profile.get("admin_eligible"),
            }
        )
    return sorted(ranked, key=lambda item: float(item["score"]), reverse=True)


def _match_payload(match: dict[str, Any], score_margin: float) -> dict[str, Any]:
    confidence = match.get("score")
    return {
        "profile_id": match.get("profile_id"),
        "speaker_public_id": match.get("speaker_public_id"),
        "display_name": match.get("display_name"),
        "confidence": confidence,
        "confidence_tier": speaker_confidence_tier(confidence),
        "score": confidence,
        "score_margin": score_margin,
        "provider": match.get("provider_id"),
        "model_id": match.get("model_id"),
        "age_band": match.get("age_band"),
        "age_restriction_class": match.get("age_restriction_class"),
        "admin_eligible": bool(match.get("admin_eligible")),
        "learning_eligible": False,
        "profile_readiness": match.get("enrollment_readiness"),
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
            "phrase_set_version",
            "phrase_tracking",
            "age_band",
            "age_restriction_class",
            "guardian_managed",
            "profile_review_interval_days",
            "last_voice_profile_review_at",
            "next_voice_profile_review_at",
            "admin_eligible",
            "profile_learning_requires_review",
            "speaker_policy",
            "created_at",
            "updated_at",
            "provider_id",
            "model_id",
            "embedding_dimensions",
            "sample_count",
            "accepted_sample_count",
            "total_accepted_speech_duration_ms",
            "enrollment_readiness",
            "enrollment_samples",
            "learning_eligible",
            "audio_retained",
        )
    }


def _profile_age_policy(profile: SpeakerProfileRequest, *, now_iso: str) -> dict[str, Any]:
    age_band = str(profile.age_band or "unknown").strip().lower()
    if age_band not in AGE_BANDS:
        age_band = "unknown"
    requested_restriction = str(profile.age_restriction_class or "").strip().lower()
    age_restriction_class = requested_restriction or AGE_RESTRICTION_CLASS[age_band]
    if age_restriction_class not in {"child", "teen", "adult", "unknown", "guardian_restricted"}:
        age_restriction_class = AGE_RESTRICTION_CLASS[age_band]
    default_interval = AGE_REVIEW_INTERVAL_DAYS[age_band]
    review_interval_days = profile.profile_review_interval_days or default_interval
    last_review_at = profile.last_voice_profile_review_at or now_iso
    next_review_at = _next_review_at(last_review_at, review_interval_days)
    admin_eligible = bool(profile.admin_eligible) and age_band == "adult"
    profile_learning_requires_review = age_band in {"child", "teen"}
    guardian_managed = bool(profile.guardian_managed) or age_band in {"child", "teen"}
    return {
        "age_band": age_band,
        "age_restriction_class": age_restriction_class,
        "guardian_managed": guardian_managed,
        "profile_review_interval_days": review_interval_days,
        "last_voice_profile_review_at": last_review_at,
        "next_voice_profile_review_at": next_review_at,
        "admin_eligible": admin_eligible,
        "profile_learning_requires_review": profile_learning_requires_review,
        "speaker_policy": {
            "schema_version": 1,
            "age_band_source": "operator_or_guardian",
            "age_inferred_from_voice": False,
            "admin_eligible": admin_eligible,
            "admin_eligibility_reason": "adult_explicitly_enabled" if admin_eligible else f"{age_band}_not_admin_eligible",
            "profile_learning_requires_review": profile_learning_requires_review,
        },
    }


def _next_review_at(last_review_at: str, interval_days: int | None) -> str | None:
    if not interval_days:
        return None
    normalized = last_review_at.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed + timedelta(days=interval_days)).isoformat()


def payload_phrase_set_version(sample: AudioSampleRequest) -> str:
    return sample.phrase_set_version or ACTIVE_SPEAKER_PHRASE_SET_VERSION


def _phrase_tracking(samples: list[dict[str, Any]], *, phrase_set_version: str) -> dict[str, Any]:
    presented = []
    accepted = []
    skipped = []
    failed_quality = []
    for sample in samples:
        phrase = {
            "phrase_set_version": sample.get("phrase_set_version") or phrase_set_version,
            "phrase_id": sample.get("phrase_id"),
            "text": sample.get("phrase_text"),
            "status": sample.get("phrase_status") or "accepted",
            "sample_id": sample.get("sample_id"),
        }
        if not (phrase["phrase_id"] or phrase["text"]):
            continue
        presented.append(phrase)
        status = str(phrase["status"])
        if status == "accepted":
            accepted.append(phrase)
        elif status == "skipped":
            skipped.append(phrase)
        elif status in {"failed_quality", "rejected"}:
            failed_quality.append(phrase)
    return {
        "schema_version": 1,
        "phrase_set_version": phrase_set_version,
        "presented": presented,
        "accepted": accepted,
        "skipped": skipped,
        "failed_quality": failed_quality,
        "used_for_validation": [],
    }


def _audio_quality_from_wav_bytes(audio_bytes: bytes) -> dict[str, Any]:
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate_hz = wav.getframerate()
            frame_count = wav.getnframes()
            pcm_bytes = wav.readframes(frame_count)
    except Exception as exc:
        raise ValueError("invalid_wav_audio") from exc
    if sample_width != 2:
        quality = analyze_pcm_s16le_audio(
            None,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            encoding=f"pcm_s{sample_width * 8}le",
        )
    else:
        quality = analyze_pcm_s16le_audio(
            pcm_bytes,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            encoding="pcm_s16le",
        )
    context = quality.as_context()
    return {
        "sample_id": None,
        "duration_ms": context["duration_ms"],
        "sample_rate_hz": sample_rate_hz,
        "channels": channels,
        "quality": {
            key: context.get(key)
            for key in (
                "status",
                "warnings",
                "duration_ms",
                "sample_rate_hz",
                "channels",
                "encoding",
                "rms",
                "peak",
                "clipping_ratio",
                "active_audio_ratio",
                "silence_ratio",
                "snr_db",
                "snr_status",
                "snr_reason",
                "source",
            )
        },
    }


def _enrollment_readiness(
    records: list[dict[str, Any]],
    *,
    expected_sample_rate_hz: int,
) -> dict[str, Any]:
    samples = [dict(record["sample"]) for record in records]
    accepted_sample_count = len(samples)
    total_duration_ms = sum(int(sample.get("audio_duration_ms") or 0) for sample in samples)
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    if accepted_sample_count < ENROLLMENT_REQUIRED_SAMPLE_COUNT:
        blocking_reasons.append("insufficient_sample_count")
    if total_duration_ms < ENROLLMENT_REQUIRED_TOTAL_DURATION_MS:
        blocking_reasons.append("insufficient_total_speech_duration")
    for index, sample in enumerate(samples, start=1):
        sample_id = sample.get("sample_id") or f"sample-{index}"
        duration_ms = int(sample.get("audio_duration_ms") or 0)
        sample_rate_hz = int(sample.get("sample_rate_hz") or 0)
        quality = sample.get("quality") if isinstance(sample.get("quality"), dict) else {}
        sample_warnings = [str(warning) for warning in quality.get("warnings") or []]
        for warning in sample_warnings:
            warnings.append(f"{sample_id}:{warning}")
        if duration_ms < ENROLLMENT_MIN_SAMPLE_DURATION_MS:
            blocking_reasons.append(f"{sample_id}:short_sample")
        if expected_sample_rate_hz and sample_rate_hz != expected_sample_rate_hz:
            blocking_reasons.append(f"{sample_id}:incompatible_sample_rate")
        if any(warning in ENROLLMENT_FATAL_WARNINGS for warning in sample_warnings):
            blocking_reasons.append(f"{sample_id}:unusable_audio")
    can_enroll = not blocking_reasons
    production_ready = (
        can_enroll
        and accepted_sample_count >= ENROLLMENT_REQUIRED_SAMPLE_COUNT
        and total_duration_ms >= ENROLLMENT_TARGET_TOTAL_DURATION_MS
        and not warnings
    )
    status = "ready" if production_ready else "usable_with_warnings" if can_enroll else "not_ready"
    return {
        "schema_version": 1,
        "status": status,
        "can_enroll": can_enroll,
        "production_ready": production_ready,
        "learning_eligible": False,
        "sample_count": len(records),
        "accepted_sample_count": accepted_sample_count,
        "required_sample_count": ENROLLMENT_REQUIRED_SAMPLE_COUNT,
        "recommended_sample_count_min": ENROLLMENT_RECOMMENDED_SAMPLE_COUNT_MIN,
        "recommended_sample_count_max": ENROLLMENT_RECOMMENDED_SAMPLE_COUNT_MAX,
        "total_accepted_speech_duration_ms": total_duration_ms,
        "required_total_speech_duration_ms": ENROLLMENT_REQUIRED_TOTAL_DURATION_MS,
        "target_total_speech_duration_ms": ENROLLMENT_TARGET_TOTAL_DURATION_MS,
        "minimum_sample_duration_ms": ENROLLMENT_MIN_SAMPLE_DURATION_MS,
        "expected_sample_rate_hz": expected_sample_rate_hz,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warnings": sorted(set(warnings)),
        "raw_audio_retained": False,
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


def _provider_not_installed_message(status: dict[str, Any]) -> str:
    provider_id = str(status.get("provider_id") or "speaker_id_provider")
    dependencies = status.get("dependencies")
    if isinstance(dependencies, dict):
        missing = [str(name) for name, available in dependencies.items() if not available]
        if missing:
            return f"{provider_id} is not installed. Missing: {', '.join(missing)}."
    reason = str(status.get("reason") or "provider_not_installed")
    return f"{provider_id} is not ready: {reason}."


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
