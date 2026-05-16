from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import socket
import sys
import urllib.request


def _post_over_unix_socket(socket_path: str, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = (
        "POST /api/engines/heartbeat HTTP/1.1\r\n"
        "Host: hexevoice-node\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + body
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(socket_path)
        client.sendall(request)
        response = client.recv(4096)
    finally:
        client.close()
    status_line = response.splitlines()[0].decode("iso-8859-1") if response else ""
    if " 2" not in status_line:
        raise RuntimeError(status_line or "empty_node_heartbeat_response")


def _post_over_url(url: str, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/engines/heartbeat",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def main() -> None:
    engine_id = os.getenv("HEXEVOICE_ENGINE_ID", "").strip()
    if not engine_id:
        raise SystemExit("missing_HEXEVOICE_ENGINE_ID")
    payload: dict[str, object] = {
        "engine_id": engine_id,
        "engine_version": os.getenv("HEXEVOICE_ENGINE_VERSION", "unknown"),
        "container_hostname": socket.gethostname(),
        "health_state": os.getenv("HEXEVOICE_ENGINE_HEALTH_STATE", "ok"),
        "config_summary": os.getenv("HEXEVOICE_ENGINE_CONFIG_SUMMARY", ""),
        "last_error": os.getenv("HEXEVOICE_ENGINE_LAST_ERROR") or None,
        "sent_at": datetime.now(UTC).isoformat(),
    }
    node_socket = os.getenv("HEXEVOICE_NODE_HEALTH_SOCKET")
    node_url = os.getenv("HEXEVOICE_NODE_HEALTH_URL")
    try:
        if node_socket:
            _post_over_unix_socket(node_socket, payload)
        elif node_url:
            _post_over_url(node_url, payload)
        else:
            raise RuntimeError("missing_node_health_target")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps({"ok": True, "engine_id": engine_id}, sort_keys=True))


if __name__ == "__main__":
    main()
