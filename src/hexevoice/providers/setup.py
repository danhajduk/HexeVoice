from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from hexevoice.api.models import ProviderSetupRequest, ProviderSetupResponse
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def voice_provider_ids(settings: Settings) -> list[str]:
    provider_ids = [settings.provider_id]
    if settings.voice_stt_provider != "deterministic":
        provider_ids.append(settings.voice_stt_provider)
    if settings.voice_tts_provider != "deterministic":
        provider_ids.append(settings.voice_tts_provider)

    normalized: list[str] = []
    seen: set[str] = set()
    for provider_id in provider_ids:
        value = str(provider_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


class ProviderSetupService:
    def __init__(self, *, settings: Settings, onboarding_state_store: OnboardingStateStore) -> None:
        self._settings = settings
        self._store = onboarding_state_store

    def status_payload(self) -> ProviderSetupResponse:
        state = self._store.load()
        supported_providers = self._supported_providers(state)
        enabled_providers = [provider_id for provider_id in state.provider_setup.enabled_providers if provider_id in supported_providers]
        blocking_reasons = self._blocking_reasons(state, enabled_providers)
        declaration_allowed = len(blocking_reasons) == 0

        return ProviderSetupResponse(
            configured=bool(enabled_providers),
            supported_providers=supported_providers,
            enabled_providers=enabled_providers,
            default_provider=state.provider_setup.default_provider,
            declaration_allowed=declaration_allowed,
            blocking_reasons=blocking_reasons,
        )

    def save_setup(self, payload: ProviderSetupRequest) -> ProviderSetupResponse:
        state = self._store.load()
        if state.trust_activation.trust_status != "trusted":
            raise HTTPException(status_code=400, detail="trust_not_ready_for_provider_setup")

        supported_providers = self._supported_providers(state)
        enabled_providers = [provider_id for provider_id in payload.enabled_providers if provider_id in supported_providers]
        default_provider = payload.default_provider or (enabled_providers[0] if enabled_providers else None)
        if default_provider and default_provider not in enabled_providers:
            raise HTTPException(status_code=400, detail="default_provider_not_enabled")

        blocking_reasons = self._blocking_reasons(state, enabled_providers)
        declaration_allowed = len(blocking_reasons) == 0
        current_step_id = "provider_setup"
        last_completed_step_id = state.resume.last_completed_step_id
        if declaration_allowed:
            current_step_id = "capability_declaration"
            last_completed_step_id = "provider_setup"

        updated = state.model_copy(
            update={
                "provider_setup": state.provider_setup.model_copy(
                    update={
                        "supported_providers": supported_providers,
                        "enabled_providers": enabled_providers,
                        "default_provider": default_provider,
                        "declaration_allowed": declaration_allowed,
                        "blocking_reasons": blocking_reasons,
                        "last_updated_at": _utc_now(),
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                ),
            }
        )
        self._store.save(updated)
        return self.status_payload()

    def _supported_providers(self, state) -> list[str]:
        persisted = [provider_id for provider_id in state.provider_setup.supported_providers if provider_id]
        runtime = voice_provider_ids(self._settings)
        supported: list[str] = []
        seen: set[str] = set()
        for provider_id in [*persisted, *runtime]:
            if provider_id in seen:
                continue
            seen.add(provider_id)
            supported.append(provider_id)
        return supported

    def _blocking_reasons(self, state, enabled_providers: list[str]) -> list[str]:
        blockers: list[str] = []
        if state.trust_activation.trust_status != "trusted":
            blockers.append("trust_not_trusted")
        if not state.trust_activation.node_id:
            blockers.append("trusted_identity_missing")
        if not enabled_providers:
            blockers.append("provider_selection_required")
        return blockers
