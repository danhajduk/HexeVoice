# Supervised openWakeWord Validation

Created: 04/25/2026

## Validated Path

The HexeVoice-managed openWakeWord container was started from this repository:

```bash
./scripts/openwakeword-control.sh start
./scripts/openwakeword-control.sh status
```

Observed runtime state:

```text
hexevoice-openwakeword Up 0.0.0.0:10400->10400/tcp
```

The container log reported:

```text
INFO:root:Ready
```

The backend supervised wake provider was then validated against the live service on `127.0.0.1:10400` with a short PCM silence frame. This proved that the backend can open the Wyoming connection, send `detect`, `audio-start`, and `audio-chunk`, and keep provider health green without persisting raw audio.

Observed provider status:

```json
{
  "provider": "supervised_openwakeword",
  "healthy": true,
  "configured": true,
  "loaded": true,
  "host": "127.0.0.1",
  "port": 10400,
  "models": ["Hexa"],
  "last_error": null
}
```

The node service status route saw the managed container:

```json
{
  "openwakeword": "running"
}
```

The targeted backend test set passed:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_voice_wake.py tests/test_voice_websocket.py tests/test_supervisor_runtime.py tests/test_api.py
```

Result:

```text
40 passed
```

## Remaining Live Validation

A true wake-to-listening acceptance test still needs a recorded or live utterance of the trained wake word. The current automated validation covers the protocol path and session transition logic with a fake Wyoming detection event, plus the live container connection with silence. It does not prove that the trained `Hexa` model detects the spoken wake word from ESP-BOX microphone audio.

The next live check should:

1. Start the managed openWakeWord container.
2. Restart the HexeVoice stack so `scripts/stack.env` selects `VOICE_WAKE_PROVIDER=supervised_openwakeword`.
3. Connect the ESP-BOX endpoint.
4. Speak the trained wake word.
5. Confirm the firmware logs show wake detection followed by listening/capturing, and `/api/voice/status` reports `wake_provider.provider=supervised_openwakeword`.
