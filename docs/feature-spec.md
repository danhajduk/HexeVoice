# Feature Spec

## Purpose

HexeVoice is a voice-runtime node for Hexe.

It is responsible for:

- monitoring live voice endpoints
- detecting wake words
- segmenting speech with VAD
- transcribing speech to text
- decoding simple local commands
- forwarding conversational text to upstream intelligence
- synthesizing spoken responses
- routing audio back to the originating endpoint

HexeVoice is a voice I/O and orchestration node.
It is not the primary owner of LLM reasoning.

## Main Goals

HexeVoice should:

- feel native on supported endpoint devices
- support more than one active endpoint
- keep endpoint state isolated per device
- support the full wake -> listen -> transcribe -> respond loop
- allow fast local command handling without always calling an LLM
- stay modular across wake, VAD, STT, TTS, endpoint adapters, and session orchestration

## Supported Endpoint Types

Initial endpoint classes:

- Home Assistant Voice
- Espressif Box

Each physical device should have its own `endpoint_id`.

Each endpoint should also carry:

- `endpoint_type`
- `zone_id`
- `display_name`
- `priority`
- `input_format`
- `output_capabilities`
- `wake_enabled`
- `stt_enabled`
- `tts_enabled`

## Native Endpoint Integration

Both Home Assistant Voice and Espressif Box should use custom YAML configuration so the integration feels native on-device rather than like a generic external audio relay.

The custom YAML should allow each endpoint type to define:

- node connection details
- endpoint identity
- wake behavior
- local audio settings
- streaming behavior
- retry behavior
- local LEDs, tones, or status indicators where supported
- preferred speech pipeline options where supported

The goal is:

- native-feeling setup
- minimal endpoint-side friction
- consistent HexeVoice behavior across device classes

## Core Runtime Features

### 1. Wake Detection

HexeVoice should host `openWakeWord` and continuously monitor incoming endpoint audio streams.

Requirements:

- per-endpoint wake detection state
- configurable wake models and thresholds
- debounce and cooldown handling
- wake event reporting with confidence and endpoint metadata

### 2. Voice Activity Detection

HexeVoice should support VAD for both wake gating and utterance segmentation.

Requirements:

- speech-presence gating during wake monitoring
- speech-start detection after wake
- end-of-speech detection for STT capture
- configurable thresholds and silence timeouts

### 3. Speech To Text

HexeVoice should host STT locally.

Initial candidate:

- Faster-Whisper

Requirements:

- endpoint-bound transcription
- support for short interactive utterances
- structured transcript results with confidence and timing where available
- degraded handling when STT is unavailable

### 4. Text To Speech

HexeVoice should host TTS locally.

Requirements:

- endpoint-bound audio output
- voice/provider selection
- low-latency short-response synthesis
- degraded handling when TTS is unavailable

### 5. Simple Command Decode

HexeVoice should decode a small set of local voice commands before sending text upstream.

Examples:

- stop
- cancel
- mute
- repeat
- status

Requirements:

- deterministic local handling
- structured command result
- no LLM dependency for simple commands

### 6. Conversation Transport

HexeVoice should support the full speech loop:

1. endpoint wake detected
2. speech captured
3. STT result produced
4. local command decode attempted
5. if not handled locally, transcript forwarded upstream
6. upstream text response received
7. TTS audio generated
8. audio returned to the originating endpoint

HexeVoice should own the voice transport pipeline but not the upstream reasoning itself.

### 7. Multi-Endpoint Monitoring

HexeVoice should monitor multiple endpoints at the same time.

Requirements:

- independent endpoint session state
- independent wake state per endpoint
- independent cooldown and capture state per endpoint
- visibility into endpoint health and activity

## Wake Collision Handling

More than one endpoint may detect the same wake word at nearly the same time.

HexeVoice should support this explicitly.

Behavior:

- detections within a short collision window should be grouped
- if endpoints are in the same `zone_id`, choose one primary endpoint
- if endpoints are in different `zone_id` values, allow separate sessions

Recommended winner selection order:

1. higher wake confidence
2. stronger recent speech/VAD evidence
3. higher endpoint priority
4. earlier arrival

Losing endpoints in the same-zone collision group should enter a short cooldown.

## Speaker Understanding

Speaker understanding is a desired future feature.

Potential support:

- speaker identification
- speaker verification
- speaker-aware personalization

This should be treated as an optional later capability, not a required MVP dependency.

## Public Capabilities

HexeVoice should expose client-usable capabilities using the format:

- `task.<capability>`

Initial capability candidates:

- `task.wake_stream`
- `task.transcribe`
- `task.synthesize`
- `task.command_interpret`
- `task.endpoint_session`
- `task.conversation_session`

These are external service capabilities.
Internal implementation details like VAD, arbitration, and provider routing should not be treated as standalone public capabilities unless intentionally exposed later.

## Endpoint Session Model

Each endpoint should have a runtime session with state such as:

- `endpoint_id`
- `endpoint_type`
- `zone_id`
- `wake_state`
- `speech_state`
- `current_session_id`
- `cooldown_until`
- `last_wake_at`
- `last_speech_at`
- `provider_status`

## Readiness Expectations

HexeVoice should not be considered fully operational unless the required voice pipeline pieces are available.

Typical readiness checks:

- trust active
- endpoint registry available
- wake provider healthy
- STT provider healthy
- TTS provider healthy
- required upstream routing available

Recommended degraded behavior:

- wake-only mode
- wake + STT but no TTS
- endpoint monitoring only

## MVP Scope

Recommended MVP:

- one wake provider
- one VAD path
- one STT provider
- one TTS provider
- Home Assistant Voice support
- Espressif Box support
- one local simple-command layer
- one upstream conversation handoff path
- multi-endpoint monitoring with same-zone arbitration

## Out Of Scope For Initial MVP

- advanced speaker recognition as a required feature
- multi-endpoint audio fusion
- local large-model reasoning
- rich endpoint-side automation beyond native YAML integration
