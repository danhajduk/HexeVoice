from __future__ import annotations

import httpx


class CoreOnboardingClient:
    def start_onboarding_session(self, *, core_base_url: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/onboarding/sessions",
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
