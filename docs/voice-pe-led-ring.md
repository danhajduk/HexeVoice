# Home Assistant Voice PE LED Ring Contract

Created: 05/09/2026

This contract covers the Home Assistant Voice Preview Edition (`ha_voice_pe`)
LED ring used by HexeVoice native firmware. It is based on the official
ESPHome Voice PE firmware configuration and the public Voice PE hardware notes.

## Scope

- Applies only to the `ha_voice_pe` firmware board profile.
- Non-PE board profiles must expose a no-op LED ring driver so voice state code
  can request patterns without adding board-specific branches.
- The LED ring is a local status surface. It must not block voice capture,
  backend heartbeats, OTA, speaker playback, or shutdown paths.

## Hardware Signals

| Signal | GPIO | Direction | Active State | Contract |
| --- | ---: | --- | --- | --- |
| LED data | `GPIO21` | output | WS2812 data | Drive with ESP-IDF RMT at WS2812 timing. Color byte order is `GRB`. |
| LED power | `GPIO45` | output | high = enabled | Keep disabled until RMT is configured. Disable on driver deinit, fatal driver error, and board shutdown. |

The ring has 12 WS2812-compatible RGB LEDs. Data line idle state should remain
low when the ring is off or power-gated.

## Ring Order

Pattern code addresses visual slots, not raw strip indices. Real-device
validation showed visual slot `0` is the bottom LED with the device upright,
and slots increase clockwise.

The physical strip order is remapped before patterns are rendered:

| Visual slot | Physical LED index |
| ---: | ---: |
| 0 | 7 |
| 1 | 8 |
| 2 | 9 |
| 3 | 10 |
| 4 | 11 |
| 5 | 0 |
| 6 | 1 |
| 7 | 2 |
| 8 | 3 |
| 9 | 4 |
| 10 | 5 |
| 11 | 6 |

Firmware should keep this mapping in the board LED driver rather than in each
animation. Pattern logic should only see the visual 0-11 ring.

## Brightness Defaults

HexeVoice firmware should use conservative LED defaults because the ring can be
visually harsh at high brightness and may be left on in bedrooms or quiet rooms.

- Default status brightness: `24/255` per channel after color scaling.
- Normal animation cap: `48/255` per channel after color scaling.
- Diagnostic/error cap: `96/255` per channel, only for short-lived alerts.
- Avoid sustained full-white output. If an all-LED state is needed, prefer a
  dim color or sparse pattern.
- Muted/privacy states should be clearly visible but still obey the normal cap.
- OTA progress and fatal-error patterns may temporarily use the diagnostic cap.

## Driver Expectations

- Initialize `GPIO45` low before enabling the RMT channel.
- Allocate one RMT LED encoder/channel for `GPIO21`.
- Apply the visual-to-physical remap while writing frame buffers.
- Advance animated patterns at a calm `100 ms` frame cadence.
- Cache the last requested pattern/state so transient reconnects do not leave
  stale LEDs on.
- Provide explicit `off`, `set_solid`, and `render_pattern` operations.
- On render failure, clear the frame buffer and power-gate the ring.
- Keep the LED update task independent of audio/VAD tasks.
- Wi-Fi and disconnected diagnostic patterns should traverse the full ring.
- Listening should keep the two side LEDs at visual slots `3` and `9` steadily on.
- During capture, the bottom visual LED should be lit orange as the fixed
  recording marker. If the endpoint is still in the listening state, capture
  should overlay the bottom orange marker while the side listening LEDs stay on.
- Pre-wake VAD/prediction sessions must stay LED-invisible. The ring should
  enter listening or thinking only after `wake.accepted` or a local
  button/manual wake starts a session.
- OTA progress should use dim completed-progress LEDs and a brighter moving
  chase LED so the current transfer activity is easy to see.

## Pattern Priority

The firmware chooses one active LED pattern every frame. Priority order is:

1. Boot while the app phase is `kBooting`.
2. OTA progress while `ota_active` is true or the app phase is `kUpdating`.
3. Muted/privacy while the hardware or software mute state is active.
4. Wi-Fi connection state while Wi-Fi is unavailable.
5. Backend connection state while heartbeat or voice WebSocket is unavailable.
6. Speaker-silent idle state when volume is `0%`.
7. Voice turn state: wake/listening with optional capture overlay, thinking,
   replying, error.
8. Idle/off when the endpoint is ready and no voice turn or diagnostic state is active.

Completed is a momentary overlay. It temporarily overrides the steady state and
then returns to the normal priority order. Cancelled sessions return directly to
the normal steady state without a dedicated LED pattern.

## Rotary Affordances

Voice PE rotary pins are `GPIO16` and `GPIO18`. Normal rotation changes the
endpoint output volume in small steps and shows a temporary LED volume meter.
Center-held rotation changes the active LED accent color and shows a temporary
full-ring color preview. A center-held rotation consumes the center-button
release so the same gesture does not wake, cancel, or long-press-cancel a voice
turn.

## OTA-Safe Behavior

- OTA progress has higher priority than voice, volume, and color affordances.
- LED render failures clear the frame buffer and power-gate the ring through
  `GPIO45`.
- Non-PE board profiles use the no-op LED ring implementation, so shared voice
  state code can call LED helpers safely on ESP-BOX builds.
- LED updates are best-effort and must not block OTA writes, audio capture, TTS
  playback, backend heartbeat, or WebSocket transport.

## Diagnostic Simulation

The backend can send `endpoint.led.simulate` through the endpoint WebSocket to
preview LED behavior without forcing real network, mute, OTA, or voice-session
state changes. Payload fields:

- `pattern`: one of `all`, `boot`, `wifi`, `backend`, `listening`, `capturing`,
  `thinking`, `replying`, `ota`, `completed`, `muted`, `speaker_silent`,
  `volume`, `color`, `error`, `disconnected`, or `off`.
- `duration_ms`: per-pattern duration from `300` to `5000` ms. `all` steps
  through every named pattern using this duration for each step.

## References

- Official ESPHome Voice PE firmware config:
  `https://github.com/esphome/home-assistant-voice-pe/blob/dev/home-assistant-voice.yaml`
- Nabu Casa support note for Voice PE internal GPIO and RGBIN/RGBOUT LED access:
  `https://support.nabucasa.com/hc/en-us/articles/25938342327581-About-the-internal-GPIO-pins`
