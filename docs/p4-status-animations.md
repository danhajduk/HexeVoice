# P4 Status Animations

The Waveshare P4 7B header supports reusable procedural animations configured by
`firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/sprites/status_layout.json`.
The media generator copies this file into the board asset library, and firmware
reloads it after asset synchronization.

## Coordinate Model

The layout has one shared screen `y` coordinate. Static icons define an absolute
screen `x`; floating icons use the shared `floating.x` origin and render in JSON
order with `floating.gap` pixels between active icons.

Animation geometry is relative to the owning sprite:

- `position.x` and `position.y`: `0.0` to `1.0` across the sprite.
- `radius`: `0.0` to `1.0` of the sprite's shorter edge.
- `spacing`: `0.0` to `1.0` of the sprite's shorter edge.

This lets one animation definition scale with 40x40 or larger sprites.

## Available Animations

### `blink_dot`

Draws a dot for half of each period. Parameters: `color`, `position`, `radius`,
and `period_ms`.

### `running_dots`

Draws up to eight horizontal dots and advances the highlighted dot. Parameters:
`color`, `position`, `count`, `radius`, `spacing`, and `period_ms`.

### `pulse`

Modulates the owning sprite's opacity with a smooth triangular cycle.
Parameters: `period_ms`, `min_opacity`, and `max_opacity` (0-255).

### `pulse_ring`

Expands and dims a procedural ring around a relative point. Parameters:
`color`, `position`, `radius`, and `period_ms`.

### `slide_in`

Moves the owning sprite into its configured position once when the condition
becomes true, then holds it in place. `offset.x` and `offset.y` are normalized
signed distances from `-1.0` to `1.0`, relative to the sprite dimensions.
`period_ms` controls the transition duration. For example, an icon entering
from one sprite-width to the left uses:

```json
{
  "type": "slide_in",
  "when": {"flag": "asset_sync_active", "equals": true},
  "offset": {"x": -1.0, "y": 0.0},
  "period_ms": 350
}
```

Every animation requires a `when` object:

```json
"when": {"flag": "asset_sync_active", "equals": true}
```

## Device Flags

Supported flags are `heartbeat`, `loading`, `wifi_connected`,
`wifi_connecting`, `backend_connected`, `backend_connecting`,
`voice_ws_connected`, `asset_sync_active`, `media_transfer_active`,
`ota_active`, `listening`, `thinking`, `replying`, `muted`, `timer_active`,
`timer_finished`, `error`, and `ui_ready`.

`heartbeat` is always true while the display runtime is alive. `loading` combines
boot, Wi-Fi connection, backend connection, asset synchronization, and OTA.
`ui_ready` becomes true only after Wi-Fi and backend connection complete, while
OTA is inactive and the application is not updating or in an error state. The
left and right sidebar sprites use this flag to slide into view.

`idle_ready` additionally requires synchronized time and the application idle
phase. It controls the large idle clock composition. While active, the large
clock replaces the small header clock. Its frame, hours, separator, minutes,
and date each have independent animation lists in `status_layout.json`.

## Deferred Animations

Candidates for later implementation:

- `spinner`: rotating arc for indeterminate work.
- `progress_ring`: numeric progress from OTA or asset synchronization.
- `sweep`: directional highlight for scanning and discovery.
- `badge_ping`: one-shot notification badge entrance.
- `shake`: short error or rejected-action movement.
- `color_cycle`: interpolation through a configured color list.

Future animation types should retain the normalized coordinate model, use the
same condition flags, and avoid introducing animation-specific device state.
