#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${STT_ENV_FILE:-$ROOT_DIR/scripts/stack.env}"
COMPOSE_FILE="$ROOT_DIR/compose.faster-whisper-stt.yaml"
CUDA_COMPOSE_FILE="$ROOT_DIR/compose.faster-whisper-stt.cuda.yaml"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

export HEXEVOICE_SOCKET_DIR="${HEXEVOICE_SOCKET_DIR:-$ROOT_DIR/runtime/sockets}"
export HEXEVOICE_STT_CACHE_DIR="${HEXEVOICE_STT_CACHE_DIR:-$ROOT_DIR/runtime/stt/faster-whisper}"
export STT_CONTAINER_NAME="${STT_CONTAINER_NAME:-hexevoice-faster-whisper-stt}"
export STT_SOCKET_PATH="${STT_SOCKET_PATH:-${VOICE_STT_SERVICE_SOCKET:-$HEXEVOICE_SOCKET_DIR/stt.sock}}"
STT_CUDA_MODE="${STT_CUDA_MODE:-auto}"
STT_CUDA_SMOKE_IMAGE="${STT_CUDA_SMOKE_IMAGE:-nvidia/cuda:12.4.1-base-ubuntu22.04}"
STT_CUDA_CHECK_TIMEOUT_S="${STT_CUDA_CHECK_TIMEOUT_S:-45}"
STT_CPU_IMAGE="${STT_CPU_IMAGE:-${STT_IMAGE:-hexevoice/faster-whisper-stt:local}}"
STT_CUDA_IMAGE="${STT_CUDA_IMAGE:-hexevoice/faster-whisper-stt:cuda}"
STT_CPU_COMPUTE_TYPE="${STT_CPU_COMPUTE_TYPE:-int8}"
STT_CUDA_COMPUTE_TYPE="${STT_CUDA_COMPUTE_TYPE:-float16}"
STT_SERVICE_URL="${STT_HEALTH_URL:-${VOICE_STT_SERVICE_BASE_URL:-http://hexevoice-stt}}"
STT_HEALTH_TIMEOUT_S="${STT_HEALTH_TIMEOUT_S:-60}"
STT_HEALTH_INTERVAL_S="${STT_HEALTH_INTERVAL_S:-2}"
STT_RUNTIME_PROFILE="cpu"
STT_RUNTIME_IMAGE_VERIFIED=false

compose() {
  local compose_args=(-f "$COMPOSE_FILE")
  if [[ "$STT_RUNTIME_PROFILE" == "cuda" ]]; then
    compose_args+=(-f "$CUDA_COMPOSE_FILE")
  fi
  "$DOCKER_BIN" compose "${compose_args[@]}" "$@"
}

service_url() {
  printf '%s' "${STT_SERVICE_URL%/}"
}

python_with_src() {
  PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$@"
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

normalized_cuda_mode() {
  if truthy "${STT_FORCE_CPU:-}" || truthy "${HEXEVOICE_STT_FORCE_CPU:-}"; then
    printf 'cpu'
    return
  fi
  if truthy "${STT_FORCE_CUDA:-}" || truthy "${HEXEVOICE_STT_FORCE_CUDA:-}"; then
    printf 'cuda'
    return
  fi
  if truthy "${STT_SKIP_CUDA_DETECTION:-}" || truthy "${HEXEVOICE_STT_SKIP_CUDA_DETECTION:-}"; then
    printf 'skip'
    return
  fi
  case "${STT_CUDA_MODE,,}" in
    auto|cpu|cuda|skip) printf '%s' "${STT_CUDA_MODE,,}" ;;
    *)
      echo "Invalid STT_CUDA_MODE=$STT_CUDA_MODE. Expected auto, cpu, cuda, or skip." >&2
      return 2
      ;;
  esac
}

apply_saved_stt_provider_config() {
  local exports
  if exports="$(python_with_src - "$ROOT_DIR" <<'PY'
from __future__ import annotations

from pathlib import Path
import json
import os
import shlex
import sys

from hexevoice.config.settings import Settings
from hexevoice.stt_profiles import resolve_stt_model_profile

root_dir = Path(sys.argv[1])
runtime_dir = Path(os.environ.get("RUNTIME_DIR") or root_dir / "runtime")
try:
    settings = Settings(runtime_dir=runtime_dir)
    state = json.loads(settings.resolved_onboarding_state_path().read_text(encoding="utf-8"))
    provider_config = (
        state.get("provider_setup", {})
        .get("provider_configs", {})
        .get("external_faster_whisper", {})
    )
except Exception:
    raise SystemExit(0)

if not isinstance(provider_config, dict):
    raise SystemExit(0)
if not any(provider_config.get(key) not in (None, "", []) for key in ("profile", "model", "device", "compute_type", "warm_model")):
    raise SystemExit(0)

try:
    profile = resolve_stt_model_profile(settings, provider_config)
except Exception:
    raise SystemExit(0)

exports = {
    "VOICE_STT_FASTER_WHISPER_MODEL": profile.model,
    "VOICE_STT_FASTER_WHISPER_DEVICE": profile.device,
    "VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE": profile.compute_type,
}
if provider_config.get("warm_model") is not None:
    exports["VOICE_STT_PRELOAD"] = "true" if provider_config.get("warm_model") else "false"

for key, value in exports.items():
    if value is not None:
        print(f"export {key}={shlex.quote(str(value))}")
PY
  )"; then
    if [[ -n "$exports" ]]; then
      eval "$exports"
    fi
  fi
}

use_cpu_runtime() {
  STT_RUNTIME_PROFILE="cpu"
  STT_RUNTIME_IMAGE_VERIFIED=false
  export STT_IMAGE="$STT_CPU_IMAGE"
  export VOICE_STT_FASTER_WHISPER_DEVICE="cpu"
  export VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE="$STT_CPU_COMPUTE_TYPE"
}

use_cuda_runtime() {
  STT_RUNTIME_PROFILE="cuda"
  STT_RUNTIME_IMAGE_VERIFIED=false
  export STT_IMAGE="$STT_CUDA_IMAGE"
  export VOICE_STT_FASTER_WHISPER_DEVICE="cuda"
  export VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE="$STT_CUDA_COMPUTE_TYPE"
}

cuda_smoke_check() {
  timeout "${STT_CUDA_CHECK_TIMEOUT_S}s" "$DOCKER_BIN" run --rm --gpus all "$STT_CUDA_SMOKE_IMAGE" nvidia-smi >/dev/null
}

cuda_image_capability_check() {
  timeout "${STT_CUDA_CHECK_TIMEOUT_S}s" "$DOCKER_BIN" run --rm --gpus all "$STT_CUDA_IMAGE" python -c '
import json
import sys

report = {"faster_whisper": False, "ctranslate2": False, "cuda_supported": False}
try:
    import faster_whisper  # noqa: F401
    report["faster_whisper"] = True
    import ctranslate2
    report["ctranslate2"] = True
    supported = set(ctranslate2.get_supported_compute_types("cuda"))
    report["supported_compute_types"] = sorted(supported)
    report["cuda_supported"] = bool(supported)
except Exception as exc:
    report["error"] = str(exc)

print(json.dumps(report, sort_keys=True))
sys.exit(0 if report["cuda_supported"] else 1)
' >/dev/null
}

select_stt_runtime() {
  local mode
  mode="$(normalized_cuda_mode)"
  case "$mode" in
    cpu)
      echo "STT CUDA detection: forced CPU runtime"
      use_cpu_runtime
      ;;
    skip)
      echo "STT CUDA detection: skipped; using CPU runtime"
      use_cpu_runtime
      ;;
    cuda)
      echo "STT CUDA detection: forced CUDA runtime"
      if ! cuda_smoke_check; then
        echo "CUDA Docker smoke check failed while STT_CUDA_MODE=cuda." >&2
        return 1
      fi
      use_cuda_runtime
      compose build
      if ! cuda_image_capability_check; then
        echo "CUDA STT image capability check failed while STT_CUDA_MODE=cuda." >&2
        return 1
      fi
      STT_RUNTIME_IMAGE_VERIFIED=true
      ;;
    auto)
      if ! cuda_smoke_check; then
        echo "STT CUDA detection: Docker GPU passthrough unavailable; using CPU runtime"
        use_cpu_runtime
        return 0
      fi
      use_cuda_runtime
      compose build
      if cuda_image_capability_check; then
        STT_RUNTIME_IMAGE_VERIFIED=true
        echo "STT CUDA detection: CUDA runtime selected"
        return 0
      fi
      echo "STT CUDA detection: CUDA STT image check failed; using CPU runtime"
      use_cpu_runtime
      ;;
  esac
}

http_request() {
  local method="$1"
  local path="$2"
  local url
  url="$(service_url)$path"
  python_with_src - "$method" "$url" "$STT_SOCKET_PATH" <<'PY'
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request

method = sys.argv[1]
url = sys.argv[2]
socket_path = sys.argv[3]
body = b"{}" if method in {"POST", "PUT"} else b""

if url.startswith("http://hexevoice-stt"):
    parsed_path = "/" + url.split("/", 3)[3] if len(url.split("/", 3)) > 3 else "/"
    request = (
        f"{method} {parsed_path} HTTP/1.1\r\n"
        "Host: hexevoice-stt\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + body
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(socket_path)
        client.sendall(request)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    finally:
        try:
            client.close()
        except Exception:
            pass
    raw = b"".join(chunks)
    header, _, payload = raw.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1") if header else ""
    status = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
    if status >= 400 or status == 0:
        print(payload.decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(status or 1)
    text = payload.decode("utf-8", errors="replace")
else:
    request = urllib.request.Request(url, method=method)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=body or None, timeout=5) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        raise SystemExit(exc.code)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

if text:
    try:
        print(json.dumps(json.loads(text), indent=2, sort_keys=True))
    except json.JSONDecodeError:
        print(text)
PY
}

wait_for_health() {
  local deadline now
  deadline=$((SECONDS + STT_HEALTH_TIMEOUT_S))
  while true; do
    if http_request GET /health >/dev/null 2>&1; then
      http_request GET /health
      return 0
    fi
    now="$SECONDS"
    if (( now >= deadline )); then
      echo "STT health check did not pass within ${STT_HEALTH_TIMEOUT_S}s at $(service_url)/health via $STT_SOCKET_PATH" >&2
      return 1
    fi
    sleep "$STT_HEALTH_INTERVAL_S"
  done
}

doctor() {
  local failed=0
  echo "STT container: $STT_CONTAINER_NAME"
  echo "STT URL: $(service_url)"
  echo "STT socket: $STT_SOCKET_PATH"
  echo "STT CUDA mode: $STT_CUDA_MODE"
  if "$DOCKER_BIN" --version >/dev/null 2>&1; then
    echo "docker: ok"
  else
    echo "docker: missing"
    failed=1
  fi
  if "$DOCKER_BIN" compose version >/dev/null 2>&1; then
    echo "docker compose: ok"
  else
    echo "docker compose: missing"
    failed=1
  fi
  if [[ -S "$STT_SOCKET_PATH" ]]; then
    echo "socket: ok"
  else
    echo "socket: unavailable (container may be stopped)"
  fi
  if http_request GET /health >/dev/null 2>&1; then
    echo "health: ok"
  else
    echo "health: unavailable"
  fi
  return "$failed"
}

cuda_preflight() {
  PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$ROOT_DIR/scripts/stt-cuda-preflight.py"
}

prepare_runtime_dirs() {
  mkdir -p "$HEXEVOICE_SOCKET_DIR" "$HEXEVOICE_STT_CACHE_DIR"
}

download_model() {
  prepare_runtime_dirs
  apply_saved_stt_provider_config
  python_with_src - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

from faster_whisper import WhisperModel

model = os.environ.get("VOICE_STT_FASTER_WHISPER_MODEL", "base")
cache_dir = Path(os.environ["HEXEVOICE_STT_CACHE_DIR"])
cache_dir.mkdir(parents=True, exist_ok=True)
WhisperModel(model, device="cpu", compute_type="int8", download_root=str(cache_dir))
print(f"downloaded {model} to {cache_dir}")
PY
}

build_if_needed() {
  if [[ "$STT_RUNTIME_IMAGE_VERIFIED" != "true" ]]; then
    compose build
  fi
}

up_build_if_needed() {
  if [[ "$STT_RUNTIME_IMAGE_VERIFIED" == "true" ]]; then
    compose up -d "$@"
  else
    compose up -d --build "$@"
  fi
}

ACTION="${1:-status}"
case "$ACTION" in
  install|build)
    prepare_runtime_dirs
    apply_saved_stt_provider_config
    select_stt_runtime
    build_if_needed
    ;;
  download-model)
    download_model
    ;;
  start)
    prepare_runtime_dirs
    apply_saved_stt_provider_config
    select_stt_runtime
    rm -f "$STT_SOCKET_PATH"
    up_build_if_needed
    ;;
  stop)
    compose stop
    ;;
  restart)
    prepare_runtime_dirs
    apply_saved_stt_provider_config
    select_stt_runtime
    rm -f "$STT_SOCKET_PATH"
    up_build_if_needed --force-recreate
    ;;
  status)
    compose ps
    ;;
  health)
    http_request GET /health
    ;;
  wait-health)
    wait_for_health
    ;;
  preload)
    http_request POST /preload
    ;;
  ready)
    prepare_runtime_dirs
    apply_saved_stt_provider_config
    select_stt_runtime
    rm -f "$STT_SOCKET_PATH"
    up_build_if_needed
    wait_for_health
    http_request POST /preload
    ;;
  doctor)
    doctor
    ;;
  cuda-preflight|preflight)
    cuda_preflight
    ;;
  logs)
    compose logs -f --tail="${2:-100}"
    ;;
  config)
    compose config
    ;;
  *)
    echo "Usage: $0 {install|build|download-model|start|stop|restart|status|health|wait-health|preload|ready|doctor|cuda-preflight|logs|config}"
    exit 1
    ;;
esac
