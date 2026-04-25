# openWakeWord Runtime Ownership

Created: 04/25/2026

## Archived External Container

The previously working wake-word runtime was owned by the HomeAssistant compose stack, not by HexeVoice:

- container name: `openwakeword`
- image: `rhasspy/wyoming-openwakeword`
- published port: `10400`
- compose project: `homeassistant`
- compose file: `/home/dan/Projects/HomeAssistant/docker-compose.yml`
- custom model mount: `/home/dan/Projects/HomeAssistant/openwakeword/models:/custom`
- previous restart policy: `unless-stopped`

On 04/25/2026 the container was stopped and its Docker restart policy was disabled:

```bash
docker update --restart=no openwakeword
docker stop openwakeword
```

The verified state after archival was:

```text
Restart=no Status=exited Exit=137
```

This prevents Docker itself from restarting the old container after host or Docker daemon restart.

## Remaining External Ownership Risk

The old service is still declared in the HomeAssistant compose file. If that external compose stack is started again, compose can recreate or start the `openwakeword` service regardless of the disabled Docker restart policy on the existing container.

HexeVoice should not edit `/home/dan/Projects/HomeAssistant` from this repository unless that cross-repo change is explicitly requested. The migration path is to add a HexeVoice-owned openWakeWord runtime and register that runtime with Core Supervisor.
