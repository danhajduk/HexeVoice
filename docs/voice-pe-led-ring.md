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

Pattern code addresses visual slots, not raw strip indices. Visual slot `0` is
the top LED with the device upright, and slots increase clockwise.

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
- Cache the last requested pattern/state so transient reconnects do not leave
  stale LEDs on.
- Provide explicit `off`, `set_solid`, and `render_pattern` operations.
- On render failure, clear the frame buffer and power-gate the ring.
- Keep the LED update task independent of audio/VAD tasks.

## References

- Official ESPHome Voice PE firmware config:
  `https://github.com/esphome/home-assistant-voice-pe/blob/dev/home-assistant-voice.yaml`
- Nabu Casa support note for Voice PE internal GPIO and RGBIN/RGBOUT LED access:
  `https://support.nabucasa.com/hc/en-us/articles/25938342327581-About-the-internal-GPIO-pins`
