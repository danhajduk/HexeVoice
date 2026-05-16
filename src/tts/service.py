from __future__ import annotations

import io
import os
from pathlib import Path
import select
import subprocess
import tempfile
import threading
import wave

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
import uvicorn


app = FastAPI(title="HexeVoice Piper TTS")
_WARM_WORKERS: dict[Path, "WarmPiperWorker"] = {}
_WARM_WORKERS_LOCK = threading.Lock()


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None


class TtsConfigRequest(BaseModel):
    default_voice: str | None = None
    warm_voices: list[str] | None = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _models_dir() -> Path:
    return Path(os.getenv("PIPER_TTS_MODEL_DIR", "/models"))


def _model_path_for_voice(voice: str | None) -> Path:
    configured = Path(os.getenv("PIPER_TTS_MODEL_PATH", str(_models_dir() / "en_US-lessac-medium.onnx")))
    if not voice:
        return configured
    resolved = _resolve_voice_model_path(voice)
    return resolved or configured


def _resolve_voice_model_path(voice: str) -> Path | None:
    safe_voice = Path(voice).name
    candidate = _models_dir() / f"{safe_voice}.onnx"
    if candidate.exists():
        return candidate
    requested = safe_voice.casefold()
    for model_path in sorted(_models_dir().glob("*.onnx")):
        if model_path.stem.casefold() == requested:
            return model_path
    return None


def _model_sample_rate(model_path: Path) -> int:
    config_path = _config_path_for_model(model_path)
    if config_path is None:
        return 22050
    try:
        import json

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        audio = payload.get("audio") if isinstance(payload, dict) else {}
        sample_rate = int(audio.get("sample_rate") or 22050)
    except Exception:
        return 22050
    return sample_rate if sample_rate > 0 else 22050


def _config_path_for_model(model_path: Path) -> Path | None:
    configured = os.getenv("PIPER_TTS_CONFIG_PATH")
    if configured:
        path = Path(configured)
        return path if path.exists() else None
    candidate = model_path.with_suffix(model_path.suffix + ".json")
    return candidate if candidate.exists() else None


def _piper_command_for_model(model_path: Path, *, output_raw: bool = False) -> list[str]:
    command = [
        os.getenv("PIPER_TTS_COMMAND", "piper"),
        "--model",
        str(model_path),
    ]
    config_path = _config_path_for_model(model_path)
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    if output_raw:
        command.append("--output-raw")
    return command


def _wav_from_raw_pcm(raw_audio: bytes, *, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_audio)
    return out.getvalue()


class WarmPiperWorker:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.sample_rate = _model_sample_rate(model_path)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        with self._lock:
            self._ensure_process_locked()
            if os.getenv("PIPER_TTS_PREWARM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}:
                self._synthesize_raw_locked(os.getenv("PIPER_TTS_PREWARM_TEXT", "warmup"))

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def synthesize_wav(self, text: str) -> bytes:
        with self._lock:
            self._ensure_process_locked()
            raw_audio = self._synthesize_raw_locked(text)
        return _wav_from_raw_pcm(raw_audio, sample_rate=self.sample_rate)

    def _ensure_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._stop_locked()
        self._process = subprocess.Popen(
            _piper_command_for_model(self.model_path, output_raw=True),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    def _synthesize_raw_locked(self, text: str) -> bytes:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("warm_piper_not_started")
        clean_text = " ".join(str(text or "").split())
        process.stdin.write(f"{clean_text}\n".encode("utf-8"))
        process.stdin.flush()

        timeout_s = _env_float("PIPER_TTS_WARM_TIMEOUT_S", 10.0)
        idle_s = _env_float("PIPER_TTS_WARM_IDLE_S", 1.0)
        started = threading.get_native_id()
        deadline = _monotonic() + timeout_s
        idle_deadline: float | None = None
        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([process.stdout], [], [], 0.02)
            now = _monotonic()
            if ready:
                chunk = os.read(process.stdout.fileno(), 8192)
                if chunk:
                    chunks.append(chunk)
                    idle_deadline = now + idle_s
                    continue
            if idle_deadline is not None and now >= idle_deadline:
                break
            if now >= deadline:
                raise RuntimeError(f"warm_piper_timeout:{self.model_path.stem}:{started}")
            if process.poll() is not None:
                raise RuntimeError(f"warm_piper_exited:{self.model_path.stem}")
        raw_audio = b"".join(chunks)
        if not raw_audio:
            raise RuntimeError(f"warm_piper_empty_audio:{self.model_path.stem}")
        return raw_audio


def _monotonic() -> float:
    import time

    return time.monotonic()


def _warm_workers() -> dict[Path, WarmPiperWorker]:
    with _WARM_WORKERS_LOCK:
        if _WARM_WORKERS:
            return _WARM_WORKERS
        for voice in _env_list("PIPER_TTS_WARM_VOICES"):
            model_path = _model_path_for_voice(voice)
            if model_path.exists():
                _WARM_WORKERS[model_path] = WarmPiperWorker(model_path)
        return _WARM_WORKERS


def _warm_worker_for_model(model_path: Path) -> WarmPiperWorker | None:
    return _warm_workers().get(model_path)


def _replace_warm_workers(voices: list[str]) -> None:
    new_workers: dict[Path, WarmPiperWorker] = {}
    for voice in voices:
        model_path = _resolve_voice_model_path(voice)
        if model_path is None:
            raise ValueError(f"unknown_voice:{voice}")
        new_workers[model_path] = WarmPiperWorker(model_path)

    with _WARM_WORKERS_LOCK:
        old_workers = list(_WARM_WORKERS.values())
        _WARM_WORKERS.clear()
        _WARM_WORKERS.update(new_workers)

    for worker in old_workers:
        worker.stop()
    for worker in new_workers.values():
        worker.start()


def synthesize_wav(*, text: str, voice: str | None = None) -> bytes:
    model_path = _model_path_for_voice(voice)
    if not model_path.exists():
        raise RuntimeError(f"missing_model:{model_path}")
    worker = _warm_worker_for_model(model_path)
    if worker is not None:
        return worker.synthesize_wav(text)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
        command = _piper_command_for_model(model_path)
        command.extend(["--output_file", str(output_path)])
        subprocess.run(
            command,
            input=f"{text}\n",
            capture_output=True,
            check=True,
            text=True,
            timeout=_env_float("PIPER_TTS_TIMEOUT_S", 30.0),
        )
        return output_path.read_bytes()
    finally:
        output_path.unlink(missing_ok=True)


@app.on_event("startup")
def warm_configured_voices() -> None:
    for worker in _warm_workers().values():
        worker.start()


@app.on_event("shutdown")
def stop_warm_voices() -> None:
    for worker in _warm_workers().values():
        worker.stop()


@app.get("/health")
def health() -> dict[str, object]:
    model_path = _model_path_for_voice(None)
    workers = _warm_workers()
    return {
        "status": "ok",
        "provider": "piper",
        "model_path": str(model_path),
        "model_exists": model_path.exists(),
        "warm_voices": [worker.model_path.stem for worker in workers.values()],
        "warm_workers": {
            worker.model_path.stem: {
                "running": worker.running(),
                "sample_rate": worker.sample_rate,
            }
            for worker in workers.values()
        },
    }


@app.post("/api/tts")
def synthesize(payload: TtsRequest) -> Response:
    try:
        audio = synthesize_wav(text=payload.text, voice=payload.voice)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or "piper_synthesis_failed"
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/wav")


@app.put("/config")
def update_config(payload: TtsConfigRequest) -> dict[str, object]:
    if payload.default_voice is not None:
        requested_default = str(payload.default_voice or "").strip()
        if requested_default:
            model_path = _resolve_voice_model_path(requested_default)
            if model_path is None:
                raise HTTPException(status_code=400, detail=f"unknown_voice:{requested_default}")
            os.environ["PIPER_TTS_MODEL_PATH"] = str(model_path)

    if payload.warm_voices is not None:
        warm_voices = []
        for voice in payload.warm_voices:
            voice_id = str(voice or "").strip()
            if voice_id and voice_id not in warm_voices:
                warm_voices.append(voice_id)
        try:
            _replace_warm_workers(warm_voices)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        os.environ["PIPER_TTS_WARM_VOICES"] = ",".join(warm_voices)

    return {
        **health(),
        "config_applied": True,
    }


def main() -> None:
    socket_path = os.getenv("PIPER_TTS_SOCKET_PATH") or os.getenv("VOICE_TTS_PIPER_SOCKET")
    if socket_path:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        uvicorn.run(app, uds=str(path))
        return
    uvicorn.run(
        app,
        host=os.getenv("PIPER_TTS_HOST", "0.0.0.0"),
        port=int(os.getenv("PIPER_TTS_PORT", "10200")),
    )


if __name__ == "__main__":
    main()
