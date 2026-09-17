# P4 Status Animations

The Waveshare P4 7B header supports reusable procedural animations configured
under `firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/config/`. The
repository keeps the editable layout configuration as YAML. Running
`firmware/tools/generate-board-media-assets.py waveshare_p4_wifi6_touch_lcd_7b`
compiles it to the JSON files consumed from the SD card:

- `items.yaml`: reusable sprites, text/data bindings, icons, sidebars, and buttons
- `animation_presets.yaml`: named animations with concrete parameters
- `button_presets.yaml`: named ordered button collections
- `screens_layout.yaml`: ordered screen index using `!include`
- `screens/*.yaml`: conditions, sidebar visibility, buttons, and placed items

The media generator validates references, expands presets, and creates the
runtime layout sections in the board asset library.
Firmware merges the listed sections in order and reloads them after asset
synchronization. A legacy monolithic `status_layout.json` remains supported.

Screen priority stays visible in the small index file while each screen can be
tuned independently. Screen items reference reusable definitions and may
override position or animations:

```yaml
schema_version: 1
screens:
  - !include screens/updating.yaml
  - !include screens/timer.yaml
  - !include screens/idle.yaml
  - !include screens/default.yaml
```

An include path is resolved relative to the file containing it. Includes may
be nested, but they cannot escape the top-level YAML file's directory and
cycles are rejected. The media generator resolves all includes before writing
the single `screens_layout.json` file consumed by the firmware.

Each screen explicitly sets `sidebars: true|false` and declares `buttons` with
either a preset or an ordered item list. The large clock and large date are
independent `big_clock` and `big_date` items.

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

```yaml
type: slide_in
when:
  flag: asset_sync_active
  equals: true
offset:
  x: -1.0
  y: 0.0
period_ms: 350
```

Every animation requires a `when` object:

```yaml
when:
  flag: asset_sync_active
  equals: true
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
phase. It selects the idle screen, whose independently placed `big_clock` and
`big_date` items replace the small header clock. Their component animations are
defined through item configuration and animation presets.

The optional `timer_screen` object keeps that idle clock visible while timers
are active. `primary_countdown` and `primary_label` configure the earliest
timer at the lower left. `upcoming` configures the lower-right list and accepts
`x`, `y`, `gap`, and `count` (up to three), plus the standard `font`,
`font_size`, and `color` text fields. Countdown values are derived locally from
the timer deadline and redraw once per second.

## Additional Animations

- `spinner`: rotating arc for indeterminate work.
- `progress_ring`: numeric OTA progress, or a repeating phase for flags without progress data.
- `sweep`: directional highlight for scanning and discovery.
- `badge_ping`: notification badge with an expanding ping.
- `shake`: short error or rejected-action movement of the owning element.
- `color_cycle`: procedural ring interpolated through two to four colors.

All geometry remains normalized to the owning element and all types use the
same condition flags without introducing animation-specific device state.
