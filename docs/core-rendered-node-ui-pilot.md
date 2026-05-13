# HexeVoice Core-Rendered Node UI Pilot

Status: Implemented

HexeVoice keeps its local node-owned UI available during the Core-rendered UI pilot. The pilot adds a node-side page manifest and page snapshot endpoints that Core can render from shared components.

## Current Surface Inventory

| Current local surface | Current files or APIs | Core card kind | Pilot endpoint |
| --- | --- | --- | --- |
| Node overview | `OverviewDashboardSection`, `/api/node/status` | `node_overview` | `/api/node/ui/overview/node` |
| Health strip | onboarding, trust, governance, provider, and runtime status APIs | `health_strip` | `/api/node/ui/overview/health` |
| Operational warnings | onboarding blockers, trust errors, governance freshness, runtime warnings | `warning_banner` | `/api/node/ui/overview/warnings` |
| Live node facts | `/api/node/status`, `/api/node/operational-status`, `/api/providers/setup` | `facts_card` | `/api/node/ui/overview/facts` |
| Runtime control | `RuntimeDashboardSection`, `/api/services/status` | `runtime_service` | `/api/node/ui/runtime/services` |
| Provider status | `/api/services/status`, `/api/voice/status`, `/api/tts/settings` | `provider_status` | `/api/node/ui/providers/status` |
| Voice endpoint status | `VoiceEndpointDashboardSection`, `/api/endpoints`, `/api/voice/status` | `record_list` | `/api/node/ui/voice/endpoints` |
| Endpoint actions | endpoint command APIs | `action_panel` | `/api/node/ui/voice/endpoint-actions` |
| Registered intents | `VoiceIntentsDashboardSection`, `/api/voice/intents` | `record_list` | `/api/node/ui/voice/intents` |
| Intent detail | `/api/voice/intents/{intent_id}` | `detail_drawer` | `/api/voice/intents/{intent_id}` |
| Intent test/invoke | `/api/voice/intents/dispatch`, `/api/voice/intents/invoke` | `action_panel` | `/api/node/ui/voice/intent-actions` |
| TTS settings and models | `TtsProviderDashboardSection`, `/api/tts/settings` | `provider_status`, `resource_grid`, `settings_form` | `/api/node/ui/voice/tts` |
| TTS artifacts | `/api/tts/artifacts`, `/api/voice/tts/artifacts` | `artifact_browser` | `/api/node/ui/voice/tts-artifacts` |
| Voice sessions and wake recordings | `/api/voice/sessions`, `/api/voice/status` | `record_list`, `artifact_browser` | `/api/node/ui/voice/sessions` |
| Endpoint media inventory | `/api/endpoint/media`, `/api/endpoint/media/inventory/{endpoint_id}` | `artifact_browser`, `resource_grid` | `/api/node/ui/voice/media` |

## Pilot Boundary

- Local React dashboard routes and components stay unchanged.
- Core receives only declarative page manifests and structured JSON page snapshots.
- Existing action endpoints stay authoritative for validation, authorization, and behavior.
- New `/api/node/ui/pages/...` endpoints are lightweight page snapshots for visible Core pages.
- Page snapshot endpoints serve the runtime JSON files from `runtime/rendered_node_ui_pages/` once those files exist.
- HexeVoice refreshes the runtime JSON files in the background on each page cadence so Core proxy reads do not block on snapshot rebuilding.
- Legacy `/api/node/ui/...` per-card endpoints remain available during migration.
- Later Core-only card kinds may be returned before Core has renderers; Core should show unsupported-card states until those kinds land.

## Implemented Pilot Endpoints

- `GET /api/node/ui-manifest`
- `GET /api/node/ui/pages/overview`
- `GET /api/node/ui/pages/runtime`
- `GET /api/node/ui/pages/voice/endpoints`
- `GET /api/node/ui/pages/voice/intents`
- `GET /api/node/ui/pages/voice/tts`
- `GET /api/node/ui/overview/node`
- `GET /api/node/ui/overview/health`
- `GET /api/node/ui/overview/warnings`
- `GET /api/node/ui/overview/facts`
- `GET /api/node/ui/runtime/services`
- `GET /api/node/ui/providers/status`
- `GET /api/node/ui/voice/endpoints`
- `GET /api/node/ui/voice/endpoint-actions`
- `GET /api/node/ui/voice/sessions`
- `GET /api/node/ui/voice/intents`
- `GET /api/node/ui/voice/intent-actions`
- `GET /api/node/ui/voice/tts`
- `GET /api/node/ui/voice/tts-artifacts`
- `GET /api/node/ui/voice/media`
- `POST /api/node/ui/actions/refresh-status`
- `POST /api/node/ui/actions/test-assistant-turn`

## Local UI Mode

`VOICE_LOCAL_UI_MODE` accepts `full`, `setup_only`, or `disabled`. The default is `full`.

During this pilot the value is reported to Core in the `node_overview` card data, but the local dashboard remains available unchanged for setup, recovery, diagnostics, and migration fallback.
