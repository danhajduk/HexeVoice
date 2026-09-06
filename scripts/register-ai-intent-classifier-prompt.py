#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


DEFAULT_PROMPT_PATH = Path("prompts/prompt.hexevoice.intent_classifier.json")
DEFAULT_TARGET_BASE_URL = "http://127.0.0.1:9002"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register or update the HexeVoice AI intent-classifier prompt.")
    parser.add_argument("--target", default=DEFAULT_TARGET_BASE_URL, help="AI node API base URL. Defaults to %(default)s.")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH, help="Prompt definition JSON path.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    target = normalize_target_base_url(args.target)
    prompt_definition = load_prompt_definition(args.prompt)
    prompt_id = str(prompt_definition["prompt_id"])
    version = str(prompt_definition["version"])
    registration_payload = prompt_registration_payload(prompt_definition)
    update_payload = prompt_update_payload(prompt_definition)

    with httpx.Client(base_url=target, timeout=args.timeout) as client:
        remote = get_remote_prompt(client, prompt_id)
        if remote is None:
            response = client.post("/api/prompts/services", json=registration_payload)
            response.raise_for_status()
            print(json.dumps({"status": "registered", "prompt_id": prompt_id, "version": version, "target": target}))
            return 0

        remote_version = remote.get("current_version") or remote.get("version")
        remote_status = str(remote.get("status") or "").strip().lower()
        if remote_version == version and remote_status == "active":
            print(json.dumps({"status": "unchanged", "prompt_id": prompt_id, "version": version, "target": target}))
            return 0

        response = client.put(f"/api/prompts/services/{prompt_id}", json=update_payload)
        response.raise_for_status()
        print(
            json.dumps(
                {
                    "status": "updated",
                    "prompt_id": prompt_id,
                    "version": version,
                    "remote_version": remote_version,
                    "remote_status": remote_status or None,
                    "target": target,
                }
            )
        )
        return 0


def normalize_target_base_url(value: str) -> str:
    target = str(value or DEFAULT_TARGET_BASE_URL).strip().rstrip("/")
    return target[:-4] if target.endswith("/api") else target


def load_prompt_definition(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Prompt definition must be an object: {path}")
    if not payload.get("prompt_id"):
        raise ValueError(f"Prompt definition is missing prompt_id: {path}")
    if not payload.get("version"):
        raise ValueError(f"Prompt definition is missing version: {path}")
    return payload


def prompt_registration_payload(prompt_definition: dict[str, Any]) -> dict[str, Any]:
    payload = dict(prompt_definition)
    payload.pop("node_runtime", None)
    return payload


def prompt_update_payload(prompt_definition: dict[str, Any]) -> dict[str, Any]:
    payload = prompt_registration_payload(prompt_definition)
    payload.pop("prompt_id", None)
    payload.pop("service_id", None)
    payload.pop("task_family", None)
    return payload


def get_remote_prompt(client: httpx.Client, prompt_id: str) -> dict[str, Any] | None:
    try:
        response = client.get(f"/api/prompts/services/{prompt_id}")
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = ""
            if detail == "prompt_id is not registered":
                return None
        if exc.response.status_code == 404:
            return None
        raise
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    return prompt if isinstance(prompt, dict) else payload


if __name__ == "__main__":
    raise SystemExit(main())
