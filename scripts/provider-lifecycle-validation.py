#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROVIDER_COMPONENTS = ("stt", "tts", "wake")
CONTROL_SCRIPTS = {
    "stt": "faster-whisper-stt-control.sh",
    "tts": "piper-tts-control.sh",
    "wake": "openwakeword-control.sh",
}


def http_json(url: str, timeout: float) -> tuple[bool, Any, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status < 500, payload, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = None
        return exc.code < 500, payload, f"HTTP {exc.code}"
    except Exception as exc:
        return False, None, str(exc)


def check_result(check_id: str, ok: bool, message: str, *, required: bool = True, detail: Any = None) -> dict[str, Any]:
    result = {
        "id": check_id,
        "status": "pass" if ok else ("fail" if required else "warn"),
        "required": required,
        "message": message,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def component_map(service_status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components = service_status.get("components") if isinstance(service_status.get("components"), list) else []
    return {
        str(component.get("component_id")): component
        for component in components
        if isinstance(component, dict) and component.get("component_id")
    }


def service_status_checks(service_status: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    components = component_map(service_status)
    for component_id in PROVIDER_COMPONENTS:
        component = components.get(component_id)
        if not component:
            checks.append(check_result(f"provider:{component_id}", False, f"{component_id} component missing"))
            continue
        ok = component.get("healthy") is not False and component.get("status") not in {"failed", "unavailable"}
        checks.append(
            check_result(
                f"provider:{component_id}",
                ok,
                f"{component_id} status={component.get('status')} healthy={component.get('healthy')}",
                detail=component,
            )
        )
        warm_model = component.get("warm_model_health")
        if isinstance(warm_model, dict):
            reload_required = bool(warm_model.get("reload_required"))
            loaded = warm_model.get("loaded")
            checks.append(
                check_result(
                    f"provider:{component_id}:warm_model",
                    not reload_required and loaded is not False,
                    f"{component_id} warm model loaded={loaded} reload_required={reload_required}",
                    required=reload_required,
                    detail=warm_model,
                )
            )
    return checks


def voice_status_checks(voice_status: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    warmup = voice_status.get("voice_tts_warmup") if isinstance(voice_status.get("voice_tts_warmup"), dict) else {}
    if warmup:
        enabled = bool(warmup.get("enabled"))
        last_error = warmup.get("last_error")
        last_run_at = warmup.get("last_run_at")
        checks.append(
            check_result(
                "tts_warmup",
                not last_error and (not enabled or bool(last_run_at)),
                f"enabled={enabled} last_run_at={last_run_at} last_error={last_error}",
                required=bool(last_error),
                detail=warmup,
            )
        )
    else:
        checks.append(check_result("tts_warmup", False, "voice_tts_warmup status missing", required=False))

    for key, check_id in (
        ("voice_artifact_cleanup", "artifact_cleanup"),
        ("voice_orphan_cleanup", "orphan_cleanup"),
    ):
        cleanup = voice_status.get(key) if isinstance(voice_status.get(key), dict) else {}
        last_error = cleanup.get("last_error") if cleanup else None
        checks.append(
            check_result(
                check_id,
                cleanup and not last_error,
                f"last_run_at={cleanup.get('last_run_at') if cleanup else None} last_error={last_error}",
                required=bool(last_error),
                detail=cleanup or None,
            )
        )
    return checks


def control_script_check(root: Path, component_id: str, timeout: float) -> dict[str, Any]:
    script = root / "scripts" / CONTROL_SCRIPTS[component_id]
    if not script.exists():
        return check_result(f"control:{component_id}:health", False, f"missing {script}")
    result = subprocess.run([str(script), "health"], text=True, capture_output=True, timeout=timeout, check=False)
    output = (result.stdout or result.stderr or "").strip()
    detail: Any = output
    if output.startswith("{"):
        try:
            detail = json.loads(output)
        except json.JSONDecodeError:
            pass
    return check_result(
        f"control:{component_id}:health",
        result.returncode == 0,
        output or f"exit {result.returncode}",
        detail=detail,
    )


def artifact_checks(root: Path, backend_url: str, timeout: float) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in ("runtime/voice_tts", "runtime/wake_recordings", "runtime/endpoint_media", "runtime/firmware"):
        path = root / rel
        count = len([item for item in path.iterdir()]) if path.exists() else 0
        checks.append(
            check_result(
                f"runtime:{rel}",
                path.exists(),
                f"{rel} {'exists' if path.exists() else 'missing'} count={count}",
                required=False,
                detail={"path": str(path), "count": count},
            )
        )

    media_ok, media_payload, media_message = http_json(f"{backend_url.rstrip('/')}/api/endpoint/media", timeout)
    checks.append(
        check_result(
            "endpoint_media_api",
            media_ok,
            media_message,
            detail=media_payload,
        )
    )
    firmware_ok, firmware_payload, firmware_message = http_json(f"{backend_url.rstrip('/')}/api/firmware/manifest", timeout)
    checks.append(
        check_result(
            "firmware_manifest",
            firmware_ok,
            firmware_message,
            required=firmware_message.startswith("HTTP 5"),
            detail=firmware_payload,
        )
    )
    return checks


def run_cycle(*, root: Path, backend_url: str, timeout: float, skip_control_scripts: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    services_ok, services_payload, services_message = http_json(f"{backend_url.rstrip('/')}/api/services/status", timeout)
    checks.append(check_result("services_status_api", services_ok, services_message, detail=services_payload))
    if services_ok and isinstance(services_payload, dict):
        checks.extend(service_status_checks(services_payload))

    voice_ok, voice_payload, voice_message = http_json(f"{backend_url.rstrip('/')}/api/voice/status", timeout)
    checks.append(check_result("voice_status_api", voice_ok, voice_message, detail=voice_payload))
    if voice_ok and isinstance(voice_payload, dict):
        checks.extend(voice_status_checks(voice_payload))

    if not skip_control_scripts:
        for component_id in PROVIDER_COMPONENTS:
            checks.append(control_script_check(root, component_id, timeout))

    checks.extend(artifact_checks(root, backend_url, timeout))
    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    return {
        "ok": not failures,
        "summary": {
            "passed": len([check for check in checks if check["status"] == "pass"]),
            "failed": len(failures),
            "warnings": len(warnings),
        },
        "checks": checks,
    }


def run_validation(*, root: Path, backend_url: str, timeout: float, cycles: int, interval_s: float, skip_control_scripts: bool) -> dict[str, Any]:
    cycle_results = []
    for index in range(cycles):
        if index and interval_s > 0:
            time.sleep(interval_s)
        result = run_cycle(root=root, backend_url=backend_url, timeout=timeout, skip_control_scripts=skip_control_scripts)
        result["cycle"] = index + 1
        cycle_results.append(result)

    failed = sum(cycle["summary"]["failed"] for cycle in cycle_results)
    warnings = sum(cycle["summary"]["warnings"] for cycle in cycle_results)
    passed = sum(cycle["summary"]["passed"] for cycle in cycle_results)
    return {
        "ok": failed == 0,
        "summary": {"passed": passed, "failed": failed, "warnings": warnings, "cycles": cycles},
        "cycles": cycle_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated HexeVoice provider/model/media lifecycle validation.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backend-url", default="http://127.0.0.1:9004")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--interval-s", type=float, default=30.0)
    parser.add_argument("--skip-control-scripts", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_validation(
        root=args.root,
        backend_url=args.backend_url,
        timeout=args.timeout,
        cycles=max(args.cycles, 1),
        interval_s=max(args.interval_s, 0.0),
        skip_control_scripts=args.skip_control_scripts,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        summary = result["summary"]
        print(
            "HexeVoice provider lifecycle validation: "
            f"ok={result['ok']} cycles={summary['cycles']} "
            f"passed={summary['passed']} failed={summary['failed']} warnings={summary['warnings']}"
        )
        for cycle in result["cycles"]:
            print(f"Cycle {cycle['cycle']}: ok={cycle['ok']}")
            for check in cycle["checks"]:
                print(f"[{check['status']}] {check['id']}: {check['message']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
