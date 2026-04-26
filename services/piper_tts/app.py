from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


app = FastAPI(title="HexeVoice Piper TTS")


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _model_path_for_voice(voice: str | None) -> Path:
    configured = Path(os.getenv("PIPER_TTS_MODEL_PATH", "/models/en_US-lessac-medium.onnx"))
    if not voice:
        return configured
    safe_voice = Path(voice).name
    candidate = Path("/models") / f"{safe_voice}.onnx"
    return candidate if candidate.exists() else configured


def _config_path_for_model(model_path: Path) -> Path | None:
    configured = os.getenv("PIPER_TTS_CONFIG_PATH")
    if configured:
        path = Path(configured)
        return path if path.exists() else None
    candidate = model_path.with_suffix(model_path.suffix + ".json")
    return candidate if candidate.exists() else None


def synthesize_wav(*, text: str, voice: str | None = None) -> bytes:
    model_path = _model_path_for_voice(voice)
    if not model_path.exists():
        raise RuntimeError(f"missing_model:{model_path}")

    command = [
        os.getenv("PIPER_TTS_COMMAND", "piper"),
        "--model",
        str(model_path),
    ]
    config_path = _config_path_for_model(model_path)
    if config_path is not None:
        command.extend(["--config", str(config_path)])

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
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


@app.get("/health")
def health() -> dict[str, object]:
    model_path = _model_path_for_voice(None)
    return {
        "status": "ok",
        "provider": "piper",
        "model_path": str(model_path),
        "model_exists": model_path.exists(),
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
