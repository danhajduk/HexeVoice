from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from hexevoice.api.models import SetupBootstrapFailure, SetupBootstrapStatusResponse
from hexevoice.config.settings import Settings


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SetupBootstrapStatusService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.runtime_dir / "setup" / "bootstrap-status.json"

    @property
    def path(self) -> Path:
        return self._path

    def status_payload(self) -> SetupBootstrapStatusResponse:
        payload = self._read_payload()
        failures = self._failure_items(payload.get("failures"))
        retryable_failures = [failure for failure in failures if failure.retryable]
        return SetupBootstrapStatusResponse(
            phase=self._text(payload.get("phase")) or "idle",
            current_action=self._text(payload.get("current_action")),
            completed_actions=self._text_list(payload.get("completed_actions")),
            pending_downloads=self._text_list(payload.get("pending_downloads")),
            failures=failures,
            retryable_failures=retryable_failures,
            final_redirect_url=self._text(payload.get("final_redirect_url")),
            temporary_setup_url=self._text(payload.get("temporary_setup_url")),
            production_setup_url=self._text(payload.get("production_setup_url")),
            lifecycle_mode=self._text(payload.get("lifecycle_mode")),
            updated_at=self._text(payload.get("updated_at")),
        )

    def _read_payload(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"phase": "idle", "updated_at": _utc_now()}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "phase": "error",
                "updated_at": _utc_now(),
                "failures": [
                    {
                        "id": "bootstrap_status_unreadable",
                        "message": f"Could not read setup bootstrap status at {self._path}.",
                        "retryable": True,
                    }
                ],
            }
        return payload if isinstance(payload, dict) else {"phase": "error", "updated_at": _utc_now()}

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _text_list(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [text for item in value if (text := cls._text(item))]

    @staticmethod
    def _failure_items(value: Any) -> list[SetupBootstrapFailure]:
        if not isinstance(value, list):
            return []
        failures: list[SetupBootstrapFailure] = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                failure_id = str(item.get("id") or f"failure_{index}").strip()
                message = str(item.get("message") or failure_id).strip()
                retryable = bool(item.get("retryable", True))
                detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
                failures.append(
                    SetupBootstrapFailure(
                        id=failure_id or f"failure_{index}",
                        message=message or "Setup bootstrap failure.",
                        retryable=retryable,
                        detail=detail,
                    )
                )
            else:
                text = str(item).strip()
                if text:
                    failures.append(SetupBootstrapFailure(id=f"failure_{index}", message=text))
        return failures
