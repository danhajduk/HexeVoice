#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def http_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_status(url: str, timeout: float) -> tuple[int | None, str | None]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location")
    except Exception:
        return None, None


def check_result(check_id: str, ok: bool, message: str, *, required: bool = True, detail: Any = None) -> dict[str, Any]:
    payload = {
        "id": check_id,
        "status": "pass" if ok else ("fail" if required else "warn"),
        "required": required,
        "message": message,
    }
    if detail is not None:
        payload["detail"] = detail
    return payload


def run_command(command: list[str], timeout: float) -> tuple[bool, str]:
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output or f"exit {result.returncode}"


def restart_stack(root: Path, timeout: float, wait_s: float) -> dict[str, Any]:
    script = root / "scripts" / "stack-control.sh"
    if not script.exists():
        return check_result("restart_stack", False, f"missing {script}")
    ok, output = run_command([str(script), "restart"], timeout)
    if wait_s > 0:
        time.sleep(wait_s)
    return check_result("restart_stack", ok, output or "stack restart completed", detail={"wait_s": wait_s})


def systemd_checks(units: list[str], timeout: float) -> list[dict[str, Any]]:
    if not shutil.which("systemctl"):
        return [check_result("systemd_units", False, "systemctl is not available", required=False)]
    checks: list[dict[str, Any]] = []
    for unit in units:
        ok, output = run_command(["systemctl", "--user", "is-active", unit], timeout)
        checks.append(check_result(f"systemd:{unit}", ok, f"{unit}: {output}", required=False))
    return checks


def docker_provider_checks(service_status: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    components = service_status.get("components") if isinstance(service_status.get("components"), list) else []
    docker_components = [
        component
        for component in components
        if isinstance(component, dict) and component.get("resource_scope") == "docker_container"
    ]
    if not docker_components:
        return [check_result("docker_providers", True, "No Docker provider components are enabled.", required=False)]

    checks = []
    for component in docker_components:
        component_id = str(component.get("component_id") or "provider")
        healthy = component.get("healthy") is not False and component.get("status") not in {"failed", "unavailable"}
        checks.append(
            check_result(
                f"docker_provider:{component_id}",
                healthy,
                f"{component_id} status={component.get('status')} healthy={component.get('healthy')}",
                detail=component,
            )
        )

    docker = shutil.which("docker")
    if docker:
        ok, output = run_command([docker, "ps", "--format", "{{.Names}}"], timeout)
        checks.append(check_result("docker_ps", ok, "docker ps succeeded" if ok else "docker ps failed", required=False, detail=output))
    else:
        checks.append(check_result("docker_ps", False, "docker executable not found", required=False))
    return checks


def verify_lifecycle(
    *,
    root: Path,
    backend_url: str,
    frontend_url: str,
    temp_url: str,
    timeout: float,
    skip_systemd: bool,
    skip_docker: bool,
    restart: bool,
    restart_wait_s: float,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    if restart:
        checks.append(restart_stack(root, max(timeout, 30.0), restart_wait_s))

    ready_url = f"{backend_url.rstrip('/')}/api/setup/ready/status"
    services_url = f"{backend_url.rstrip('/')}/api/services/status"

    ready_status: dict[str, Any] = {}
    try:
        ready_status = http_json(ready_url, timeout)
        ready_ok = (
            ready_status.get("completed") is True
            and ready_status.get("operational_ready") is True
            and ready_status.get("setup_root_redirect_active") is False
        )
        checks.append(
            check_result(
                "setup_completed_state",
                ready_ok,
                "setup complete state is operational"
                if ready_ok
                else "setup is not in completed operational state",
                detail={
                    "completed": ready_status.get("completed"),
                    "operational_ready": ready_status.get("operational_ready"),
                    "setup_root_redirect_active": ready_status.get("setup_root_redirect_active"),
                },
            )
        )
    except Exception as exc:
        checks.append(check_result("setup_completed_state", False, f"ready status unavailable: {exc}"))

    service_status: dict[str, Any] = {}
    try:
        service_status = http_json(services_url, timeout)
        components = service_status.get("components") if isinstance(service_status.get("components"), list) else []
        component_map = {component.get("component_id"): component for component in components if isinstance(component, dict)}
        for component_id in ("backend", "stt", "tts", "wake"):
            component = component_map.get(component_id) or {}
            healthy = bool(component) and component.get("healthy") is not False and component.get("status") not in {"failed", "unavailable"}
            checks.append(
                check_result(
                    f"production_service:{component_id}",
                    healthy,
                    f"{component_id} status={component.get('status')} healthy={component.get('healthy')}",
                    detail=component,
                )
            )
        supervisor = service_status.get("supervisor") if isinstance(service_status.get("supervisor"), dict) else {}
        supervisor_configured = bool(supervisor.get("configured"))
        supervisor_ok = not supervisor_configured or (bool(supervisor.get("registered")) and not supervisor.get("last_error"))
        checks.append(
            check_result(
                "supervisor_registration",
                supervisor_ok,
                "Supervisor is registered." if supervisor_ok else "Supervisor is configured but not registered cleanly.",
                required=supervisor_configured,
                detail=supervisor,
            )
        )
    except Exception as exc:
        checks.append(check_result("production_services", False, f"service status unavailable: {exc}"))

    frontend_status, _ = http_status(frontend_url, timeout)
    checks.append(
        check_result(
            "production_frontend",
            frontend_status is not None and frontend_status < 500,
            f"frontend HTTP {frontend_status}" if frontend_status is not None else "frontend unavailable",
        )
    )

    temp_status, temp_location = http_status(temp_url, timeout)
    checks.append(
        check_result(
            "temporary_runner_shutdown",
            temp_status is None,
            "temporary setup runner is stopped"
            if temp_status is None
            else f"temporary setup runner still responds HTTP {temp_status}",
            required=temp_status not in {301, 302, 303, 307, 308},
            detail={"status": temp_status, "location": temp_location},
        )
    )

    if not skip_systemd:
        checks.extend(systemd_checks(["hexevoice-backend.service", "hexevoice-frontend.service", "hexevoice-stt.service"], timeout))

    if not skip_docker:
        checks.extend(docker_provider_checks(service_status, timeout))

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify HexeVoice post-complete lifecycle after setup.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backend-url", default="http://127.0.0.1:9004")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:8084/")
    parser.add_argument("--temp-url", default="http://127.0.0.1:8180/setup/host")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--restart-stack", action="store_true")
    parser.add_argument("--restart-wait-s", type=float, default=8.0)
    parser.add_argument("--skip-systemd", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = verify_lifecycle(
        root=args.root,
        backend_url=args.backend_url,
        frontend_url=args.frontend_url,
        temp_url=args.temp_url,
        timeout=args.timeout,
        skip_systemd=args.skip_systemd,
        skip_docker=args.skip_docker,
        restart=args.restart_stack,
        restart_wait_s=args.restart_wait_s,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        summary = result["summary"]
        print(
            f"HexeVoice post-complete lifecycle: ok={result['ok']} "
            f"passed={summary['passed']} failed={summary['failed']} warnings={summary['warnings']}"
        )
        for check in result["checks"]:
            print(f"[{check['status']}] {check['id']}: {check['message']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
