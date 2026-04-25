# 🔮 HexeVoice Roadmap v3 (Wake-Driven Architecture)

## Core Shift (Important)

This roadmap assumes:

> **Wake word detection = backend (openWakeWord)**
> **Firmware = audio + UX + transport only**

That changes everything—in a good way.

---

# 🧭 Phase 0 — Baseline (Reality Check)

**Goal:** Confirm what is real vs scaffold.

Current baseline:

* Node onboarding / trust / lifecycle: implemented
* Core Supervisor runtime registration and heartbeat: implemented
* Dashboard shell: implemented
* ESP32 mic + VAD loop: partial
* Text assistant endpoint: implemented
* Voice pipeline: missing

See `docs/voice-node-phase-0-baseline.md` and `docs/firmware-baseline.md` for the detailed current-state record.

---

# 🎯 Phase 1 — First Real Voice Loop (Wake → Speak → Reply)

## Goal

One complete, reliable, demoable voice interaction.

---

## 🧠 Architecture (Phase 1)

### Backend (authority)

* openWakeWord (wake detection)
* session lifecycle
* STT
* assistant call (`/api/assistant/turn`)
* TTS
* event routing

### Firmware (endpoint)

* continuous mic capture (ring buffer or chunked stream)
* simple VAD (optional assist only)
* send audio upstream
* play TTS audio
* show state (idle/listening/thinking/speaking)
* button fallback (stop/mute)

---

## 🔁 Flow (Canonical)

```text
ESP32 streams audio → backend

backend:
  openWakeWord detects "Hexe"
  → session.start
  → wake.accepted

ESP32:
  → enters listening state

ESP32:
  → sends utterance audio (full chunk)

backend:
  → STT
  → assistant turn
  → TTS

backend:
  → sends response

ESP32:
  → plays audio
```

---

## 📦 Transport (Phase 1)

Use **WebSocket**, but keep it simple:

### Messages:

```json
session.start
audio.stream (or audio.chunk)
audio.end
transcript.final
response.text
tts.ready
session.complete
session.error
```

👉 No true real-time streaming yet
👉 No partial transcripts yet
👉 No duplex complexity yet

---

## 🧾 Deliverables

Backend:

* `/api/voice/ws`
* basic session manager (single session per endpoint)
* openWakeWord integration
* STT + TTS wired
* event envelope system

Firmware:

* audio upload
* playback path
* simple UI state

UI:

* show:

  * endpoint status
  * last transcript
  * last response
  * last error

---

## ✅ Phase 1 Success Criteria

You can say:

> “Hexe, what time is it?”

…and the box answers.

---

# 🧩 Phase 2 — Endpoint + Session Contract

Now that it works, formalize it.

---

## 🎯 Goal

Turn working behavior into a real protocol.

---

## 🔑 Key Decisions

### 1. Endpoint Registration

Persist:

* endpoint_id
* zone_id
* display_name
* firmware_version
* capabilities

---

### 2. Session Lifecycle (Backend)

Keep it expressive internally:

```text
wake_detected
→ listening
→ capturing
→ transcribing
→ routing
→ responding
→ completed
```

---

### 3. State Separation (Important)

Split into:

```text
connection_state
ux_state
session_state
```

You already identified this correctly:

> “do not force one enum to represent everything”

---

### 4. Event Envelope

Everything becomes:

```json
{
  "event_type": "...",
  "session_id": "...",
  "endpoint_id": "...",
  "timestamp": "...",
  "payload": {}
}
```

---

### 5. Persistence

Store:

* endpoint registry
* last_seen
* last_session_summary
* last_error

Do NOT store:

* raw audio
* full transcript history (yet)

---

# ⚙️ Phase 3 — Voice Pipeline (Backend Modules)

Now clean up the mess into real structure.

---

## 📦 Modules

```text
voice/
  session_manager.py
  transport.py
  wake.py        (openWakeWord wrapper)
  vad.py         (optional refinement)
  stt.py
  commands.py
  router.py
  tts.py
  events.py
```

---

## 🎯 Capabilities

* proper session state machine
* local command handling:

  * stop
  * repeat
  * mute
  * status
* upstream routing boundary
* per-session event log

---

## 🧠 Important Design Rule

Voice Node = orchestration, not intelligence.

Keep it separate from AI Node responsibilities long-term.

---

# 📡 Phase 4 — Firmware Runtime (Make It Solid)

Make the ESP32 endpoint reliable, not smart.

---

## Responsibilities

* Wi-Fi reconnect
* backend reconnect
* continuous audio capture
* bounded buffering
* playback
* state display
* Voice Node-managed firmware OTA:

  * track endpoint firmware version
  * host or reference firmware artifacts
  * expose update availability and update status
  * send OTA/update commands to the endpoint
  * endpoint downloads firmware from a Voice Node URL
  * report update progress and result
  * keep failure and rollback behavior safe
* button controls:

  * stop
  * retry
  * mute

---

## Non-Responsibilities

* STT
* wake word (for now)
* LLM logic
* policy

---

## ⚠️ Note on VAD

Current VAD is useful:

> “useful as an early signal, not full segmentation”

Keep it as helper only.

---

# 🖥️ Phase 5 — Operator Visibility

Replace dashboard placeholders with real telemetry.

---

## UI Components

* endpoint list
* active session card
* speech pipeline timeline:

  * wake
  * capture
  * transcript
  * routing
  * response
* last transcript / response
* errors

---

## Controls

* stop session
* replay response
* mute endpoint
* reconnect endpoint

---

# 🧠 Phase 6 — Multi-Endpoint

Only after single endpoint is rock solid.

---

## Add

* endpoint priority
* zone_id
* collision handling
* cooldown rules
* concurrency policy

---

# 🔒 Phase 7 — Hardening & Privacy

Production safety.

---

## Add

* transcript retention policy
* no raw audio storage (default)
* redaction in logs
* queue limits
* crash-safe sessions
* degraded behavior

---

## Tie-in with Node Runtime

Nodes can already enter degraded states:

> failures can move node into “degraded”

Now reflect that in voice UX.

---
