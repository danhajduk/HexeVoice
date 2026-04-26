FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIPER_TTS_MODEL_PATH=/models/en_US-lessac-medium.onnx
ENV PIPER_TTS_TIMEOUT_S=30

RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn piper-tts

WORKDIR /app
COPY services/piper_tts/app.py /app/app.py

EXPOSE 10200

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10200"]
