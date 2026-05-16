#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def http_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_ok(url: str, timeout: float) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


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


def run_control_script(path: Path, action: str, timeout: float) -> tuple[bool, str, Any]:
    if not path.exists():
        return False, f"missing {path}", None
    result = subprocess.run(
        [str(path), action],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = (result.stdout or result.stderr or "").strip()
    detail: Any = output
    if output.startswith("{"):
        try:
            detail = json.loads(output)
        except json.JSONDecodeError:
            pass
    return result.returncode == 0, output or f"exit {result.returncode}", detail


def smoke_test(
    *,
    root: Path,
    backend_url: str,
    frontend_url: str,
    timeout: float,
    check_docker: bool,
    check_host_alias: bool,
    hosts_path: Path,
    host_alias: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    try:
        service_status = http_json(f"{backend_url.rstrip('/')}/api/services/status", timeout)
        backend_ok = service_status.get("backend") == "running"
        checks.append(check_result("backend", backend_ok, f"backend={service_status.get('backend')}", detail=service_status.get("components")))
        for component_id in ("stt", "tts", "wake"):
            component = next(
                (item for item in service_status.get("components", []) if item.get("component_id") == component_id),
                {},
            )
            ok = component.get("status") == "running" and bool(component.get("healthy", True))
            checks.append(
                check_result(
                    f"{component_id}_status",
                    ok,
                    f"{component_id} status={component.get('status')} healthy={component.get('healthy')}",
                    detail=component,
                )
            )
    except Exception as exc:
        checks.append(check_result("backend", False, f"backend status unavailable: {exc}"))

    frontend_ok, frontend_message = http_ok(frontend_url, timeout)
    checks.append(check_result("frontend", frontend_ok, frontend_message))

    for rel in ("runtime/sockets", "runtime/firmware", "runtime/stt/faster-whisper", "runtime/piper-tts/models", "runtime/openwakeword/models"):
        path = root / rel
        checks.append(check_result(f"dir:{rel}", path.exists(), f"{rel} {'exists' if path.exists() else 'missing'}", required=False))

    if check_host_alias:
        try:
            hosts_text = hosts_path.read_text(encoding="utf-8")
            present = f" {host_alias} " in f" {hosts_text.replace(chr(9), ' ')} "
            checks.append(
                check_result(
                    "host_alias",
                    present,
                    f"{host_alias} {'present' if present else 'not present'} in {hosts_path}",
                    required=False,
                )
            )
        except Exception as exc:
            checks.append(check_result("host_alias", False, f"host alias check unavailable: {exc}", required=False))

    for check_id, script in (
        ("stt_health", "faster-whisper-stt-control.sh"),
        ("tts_health", "piper-tts-control.sh"),
        ("wake_health", "openwakeword-control.sh"),
    ):
        ok, message, detail = run_control_script(root / "scripts" / script, "health", timeout)
        checks.append(check_result(check_id, ok, message, detail=detail))

    if check_docker:
        docker = shutil.which("docker")
        if docker:
            result = subprocess.run([docker, "ps", "--format", "{{.Names}}"], text=True, capture_output=True, timeout=timeout, check=False)
            checks.append(
                check_result(
                    "docker",
                    result.returncode == 0,
                    "docker ps succeeded" if result.returncode == 0 else "docker ps failed",
                    required=False,
                    detail=(result.stdout or result.stderr or "").strip(),
                )
            )
        else:
            checks.append(check_result("docker", False, "docker executable not found", required=False))

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
    parser = argparse.ArgumentParser(description="Run HexeVoice post-install smoke checks.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backend-url", default="http://127.0.0.1:9004")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:8084/")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--check-host-alias", action="store_true")
    parser.add_argument("--hosts-path", type=Path, default=Path("/etc/hosts"))
    parser.add_argument("--host-alias", default="HexeVoice")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = smoke_test(
        root=args.root,
        backend_url=args.backend_url,
        frontend_url=args.frontend_url,
        timeout=args.timeout,
        check_docker=not args.skip_docker,
        check_host_alias=args.check_host_alias,
        hosts_path=args.hosts_path,
        host_alias=args.host_alias,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        summary = result["summary"]
        print(f"HexeVoice smoke test: ok={result['ok']} passed={summary['passed']} failed={summary['failed']} warnings={summary['warnings']}")
        for check in result["checks"]:
            print(f"[{check['status']}] {check['id']}: {check['message']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
